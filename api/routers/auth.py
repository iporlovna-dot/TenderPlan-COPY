"""Аутентификация: регистрация компании и логин. Secure by design.

- пароли — argon2id (`security.hash_password`);
- единый ответ на неверный логин/пароль (без утечки существования аккаунта);
- rate-limit на (IP+email) с временной блокировкой (защита от перебора);
- company создаётся вместе с первым пользователем (owner).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.models import Company, RefreshToken, User
from api.schemas import LoginIn, RefreshIn, RegisterIn, TokenOut
from api.security import (create_access_token, hash_password, hash_token, login_limiter,
                          new_refresh_token, refresh_expiry, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])

# Единый ответ — не раскрываем, что именно не так (email нет / пароль неверен).
_BAD_CREDS = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                           detail="Неверный логин или пароль")
_BAD_TOKEN = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                          detail="Недействительный refresh-токен")


def _issue_tokens(db: Session, user: User, family_id: str = "") -> TokenOut:
    """Выдать пару access+refresh. refresh — опаковый, в БД только хэш; family_id связывает линию
    ротации (для reuse-detection). Новый вход → новое семейство; refresh → то же семейство."""
    fam = family_id or uuid.uuid4().hex
    plaintext, thash = new_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=thash, family_id=fam, expires_at=refresh_expiry()))
    db.commit()
    return TokenOut(access_token=create_access_token(str(user.id), user.company_id),
                    refresh_token=plaintext)


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    email = data.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        # существование аккаунта на регистрации скрыть нельзя (email уникален), но пароль не трогаем
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Пользователь с таким email уже существует")
    company = Company(name=data.company_name.strip())
    db.add(company)
    db.flush()
    user = User(email=email, password_hash=hash_password(data.password), company_id=company.id)
    db.add(user)
    db.commit()
    return _issue_tokens(db, user)


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = data.email.lower().strip()
    ip = request.client.host if request.client else "?"
    key = "%s|%s" % (ip, email)

    locked = login_limiter.locked_for(key)
    if locked > 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Слишком много попыток. Повторите через %d с." % int(locked))

    user = db.query(User).filter(User.email == email).first()
    # verify всегда (постоянное время): даже если юзера нет — сверяем с dummy, чтобы не
    # различать «нет email» и «неверный пароль» по времени ответа.
    ok = verify_password(data.password, user.password_hash) if user else \
        verify_password(data.password, _DUMMY_HASH)
    if not user or not ok:
        login_limiter.record_fail(key)
        raise _BAD_CREDS

    login_limiter.reset(key)
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenOut)
def refresh(data: RefreshIn, db: Session = Depends(get_db)):
    """Обменять refresh на новую пару (РОТАЦИЯ: старый refresh становится недействителен).

    Reuse-detection: если предъявлен УЖЕ использованный refresh (был ротирован) — это признак
    кражи (легитимный клиент такого не делает), отзываем ВСЁ семейство токенов → и вор, и жертва
    разлогинены, жертва просто входит заново."""
    tok = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(data.refresh_token)).first()
    if tok is None or tok.revoked or tok.expires_at < datetime.utcnow():
        raise _BAD_TOKEN
    if tok.used:                                         # повтор использованного → кража
        db.query(RefreshToken).filter(RefreshToken.family_id == tok.family_id) \
            .update({"revoked": True})
        db.commit()
        raise _BAD_TOKEN
    tok.used = True                                      # ротация: гасим текущий
    db.commit()
    user = db.get(User, tok.user_id)
    if user is None:
        raise _BAD_TOKEN
    return _issue_tokens(db, user, family_id=tok.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: RefreshIn, db: Session = Depends(get_db)):
    """Отозвать refresh-токен (выход). Идемпотентно: неизвестный токен → тоже 204 (без утечки)."""
    db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(data.refresh_token)) \
        .update({"revoked": True})
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# фиктивный хеш для сверки при отсутствии пользователя (защита от timing-атаки)
_DUMMY_HASH = hash_password("specmatch-dummy-password-for-constant-time")
