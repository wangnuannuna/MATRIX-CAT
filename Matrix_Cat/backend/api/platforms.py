# -*- coding: utf-8 -*-
from fastapi import APIRouter

from ..config import PLATFORMS

router = APIRouter(tags=["platforms"])


@router.get("/platforms")
def platforms():
    return PLATFORMS
