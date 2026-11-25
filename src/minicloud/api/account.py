import secrets

from fastapi import APIRouter, Request

from ..errors import Forbidden
from ..ratelimit import TooManyRequests, client_ip
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, MeOut, SignupIn, SignupOut
from ..services import keys, tenancy
from .deps import DB, AppSettings, CurrentUser

router = APIRouter(tags=["account"])


@router.post("/signup", response_model=SignupOut, status_code=201)
def signup(body: SignupIn, db: DB, settings: AppSettings, request: Request) -> SignupOut:
    """Tự đăng ký: tạo tài khoản, tổ chức và project `default`.

    Tặng credit dùng thử và trả về API key (chỉ hiện một lần). Khi nhóm vận hành bật mã mời,
    phải gửi kèm `invite_code` đúng. Mỗi địa chỉ IP chỉ được thử đăng ký vài lần mỗi giờ.
    """
    limiter = request.app.state.signup_limiter
    ip = client_ip(request)
    if limiter.exceeded(ip):
        raise TooManyRequests(
            "too many sign-ups from this address: try again later", limiter.retry_after(ip)
        )
    limiter.hit(ip)  # every attempt counts, so wrong invite codes cannot be tried quickly
    expected = settings.signup_invite_code
    if expected and not secrets.compare_digest(
        (body.invite_code or "").encode(), expected.encode()
    ):
        raise Forbidden("sign-up needs a valid invite code from the operators")
    user, org, project, raw_key = tenancy.signup(
        db,
        email=body.email,
        name=body.name,
        org_name=body.org_name,
        trial_credits=settings.trial_credits,
    )
    return SignupOut(
        user_id=user.id,
        org_id=org.id,
        project_id=project.id,
        api_key=raw_key,
        credits=org.balance_millicredits / 1000,
    )


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return MeOut(
        id=user.id, email=user.email, name=user.name, is_platform_admin=user.is_platform_admin
    )


def _key_out(key, request: Request) -> ApiKeyOut:
    out = ApiKeyOut.model_validate(key)
    out.current = key.id == getattr(request.state, "api_key_id", None)
    return out


@router.get("/me/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(user: CurrentUser, db: DB, request: Request) -> list[ApiKeyOut]:
    """Các API key của bạn, kể cả key đã thu hồi. Không bao giờ trả lại giá trị đầy đủ của key."""
    return [_key_out(k, request) for k in keys.list_keys(db, user)]


@router.post("/me/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    body: ApiKeyCreate, user: CurrentUser, db: DB, request: Request
) -> ApiKeyCreated:
    """Tạo thêm một key có tên, ví dụ cho một script. Giá trị key chỉ hiện một lần."""
    key, raw = keys.create_key(db, user, body.name)
    return ApiKeyCreated(key=_key_out(key, request), api_key=raw)


@router.delete("/me/api-keys/{key_id}", response_model=ApiKeyOut)
def revoke_api_key(key_id: str, user: CurrentUser, db: DB, request: Request) -> ApiKeyOut:
    """Thu hồi một key: từ lúc này key đó bị từ chối.

    Không thu hồi được key còn hoạt động cuối cùng, vì key là cách duy nhất để vào tài khoản.
    """
    return _key_out(keys.revoke_key(db, user, key_id), request)
