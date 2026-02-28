"""인증 라우터 — 로그인 및 토큰 검증."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import bcrypt
from jose import jwt, JWTError, ExpiredSignatureError
from config import Config

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=Config.JWT_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)


def verify_token(token: str) -> str:
    """토큰 검증 후 username 반환. 실패 시 ValueError."""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise ValueError("invalid token")
        return username
    except ExpiredSignatureError:
        raise ValueError("token expired")
    except JWTError:
        raise ValueError("invalid token")


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    """아이디/비밀번호 검증 후 JWT 토큰 반환."""
    valid_user = body.username == Config.AUTH_USERNAME
    valid_pass = _verify_password(body.password, Config.AUTH_PASSWORD_HASH)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    token = create_access_token(body.username)
    return TokenResponse(
        access_token=token,
        expires_in=Config.JWT_EXPIRE_HOURS * 3600,
    )


@router.get("/verify")
def verify(token: str):
    """토큰 유효성 확인 (선택적 사용)."""
    try:
        username = verify_token(token)
        return {"valid": True, "username": username}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
