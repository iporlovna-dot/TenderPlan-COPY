"""Зависимости FastAPI: текущий пользователь из Bearer-токена (+ его company_id).

Изоляция арендаторов: company_id берём ТОЛЬКО из токена, никогда из тела запроса —
клиент не может обратиться к чужой компании.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from api.db import get_db
from api.models import User
from api.security import decode_access_token

_UNAUTH = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Не авторизован", headers={"WWW-Authenticate": "Bearer"})

# HTTPBearer → Swagger показывает кнопку Authorize (замочек) и сам подставляет токен.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(cred: HTTPAuthorizationCredentials = Depends(_bearer),
                     db: Session = Depends(get_db)) -> User:
    if cred is None or not cred.credentials:
        raise _UNAUTH
    payload = decode_access_token(cred.credentials)
    if not payload or "sub" not in payload:
        raise _UNAUTH
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if user is None or user.company_id != payload.get("cid"):
        raise _UNAUTH
    return user
