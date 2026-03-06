"""인증 라우터 — 로그인 및 토큰 검증."""
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import bcrypt
from jose import jwt, JWTError, ExpiredSignatureError
from config import Config

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenVerifyResponse(BaseModel):
    valid: bool
    username: str


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


@router.post("/login")
def login(body: LoginRequest, response: Response):
    """아이디/비밀번호 검증 후 httpOnly 쿠키로 JWT 설정."""
    valid_user = body.username == Config.AUTH_USERNAME
    valid_pass = _verify_password(body.password, Config.AUTH_PASSWORD_HASH)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    token = create_access_token(body.username)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,  # 30일
        secure=False,             # HTTP 서버; HTTPS면 True로
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    """쿠키 삭제."""
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/verify", response_model=TokenVerifyResponse)
def verify(token: str) -> TokenVerifyResponse:
    """토큰 유효성 확인 (선택적 사용)."""
    try:
        username = verify_token(token)
        return TokenVerifyResponse(valid=True, username=username)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
