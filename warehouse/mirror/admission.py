from warehouse.mirror.apply import InstallResult, apply_release
from warehouse.mirror.repository import MirrorRepository
from warehouse.mirror.review_apply import apply_review_message
from warehouse.mirror.review_repository import MirrorReviewRepository
from warehouse.mirror.verification import verify_v1_ledger
from warehouse.release.format import decode_v1


def install_release(repository: MirrorRepository, artifact: bytes) -> InstallResult:
    manifest, records = decode_v1(artifact)
    verified = verify_v1_ledger(manifest, records, artifact)
    return apply_release(repository, verified)


def install_review_message(repository: MirrorReviewRepository, artifact: bytes) -> None:
    apply_review_message(repository, artifact)
    return None
