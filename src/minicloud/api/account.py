import secrets

from fastapi import APIRouter

from ..errors import Forbidden
from ..schemas import MeOut, SignupIn, SignupOut
from ..services import tenancy
from .deps import DB, AppSettings, CurrentUser

router = APIRouter(tags=["account"])


@router.post("/signup", response_model=SignupOut, status_code=201)
def signup(body: SignupIn, db: DB, settings: AppSettings) -> SignupOut:
    """Tự đăng ký: tạo tài khoản, tổ chức và project `default`.

    Tặng credit dùng thử và trả về API key (chỉ hiện một lần). Khi nhóm vận hành bật mã mời,
    phải gửi kèm `invite_code` đúng.
    """
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
