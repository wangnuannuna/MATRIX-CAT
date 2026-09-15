# -*- coding: utf-8 -*-
"""平台用户:注册 / 登录 / 我的资料 / 改密码。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud
from ..db import get_db
from ..models import User
from ..schemas import PasswordChangeIn, ProfileIn, RegisterIn, UserLoginIn

router = APIRouter(tags=["auth"])


@router.post("/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if body.phone and crud.get_user_by_phone(db, body.phone):
        raise HTTPException(409, "手机号已被注册")
    if crud.get_user_by_username(db, body.username):
        raise HTTPException(409, "该账号已被占用")
    if body.email and crud.get_user_by_email(db, body.email):
        raise HTTPException(409, "邮箱已被注册")
    u = crud.create_user(db, body.username, body.password,
                         email=body.email, phone=body.phone, nickname=body.nickname)
    return {"ok": True, "token": auth.make_token(u.id), "user": crud.user_public(u)}


@router.post("/auth/login")
def login(body: UserLoginIn, db: Session = Depends(get_db)):
    u = crud.get_user_by_login(db, body.username)
    if not u or not auth.verify_password(body.password, u.password_hash):
        raise HTTPException(401, "手机号或密码不对")
    if u.status != "active":
        raise HTTPException(403, "账号已停用")
    crud.touch_login(db, u)
    return {"ok": True, "token": auth.make_token(u.id), "user": crud.user_public(u)}


@router.get("/auth/me")
def me(user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    return {"ok": True, "user": crud.user_public(user), "membership": crud.membership_status(db, user)}


@router.patch("/auth/me")
def update_me(body: ProfileIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    if body.email and body.email != user.email and crud.get_user_by_email(db, body.email):
        raise HTTPException(409, "邮箱已被别人用了")
    u = crud.update_user_profile(db, user, nickname=body.nickname, email=body.email,
                                 phone=body.phone, avatar=body.avatar)
    return {"ok": True, "user": crud.user_public(u)}


@router.post("/auth/password")
def change_password(body: PasswordChangeIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    if not auth.verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码不对")
    crud.change_password(db, user, body.new_password)
    return {"ok": True}
