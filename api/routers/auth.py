"""Аутентификация: регистрация компании и логин. Secure by design.

- пароли — argon2id (`security.hash_password`);
- единый ответ на неверный логин/пароль (без утечки существования аккаунта);
- rate-limit на (IP+email) с временной блокировкой (защита от перебора);
- company создаётся вместе с первым пользователем (owner).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.models import Company, User
from api.schemas import LoginIn, RegisterIn, TokenOut
from api.security import (create_access_token, hash_password, login_limiter,
                          verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])

# Единый ответ — не раскрываем, что именно не так (email нет / пароль неверен).
_BAD_CREDS = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                           detail="Неверный логин или пароль")


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
    return TokenOut(access_token=create_access_token(str(user.id), company.id))


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
    return TokenOut(access_token=create_access_token(str(user.id), user.company_id))


# фиктивный хеш для сверки при отсутствии пользователя (защита от timing-атаки)
_DUMMY_HASH = hash_password("specmatch-dummy-password-for-constant-time")
