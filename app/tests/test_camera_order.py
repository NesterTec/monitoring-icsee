import nvr.db as db_module
from nvr.models import Camera
from nvr.security import encrypt_secret


def _add_cam(db, name: str, sort_order: int) -> Camera:
    cam = Camera(
        name=name,
        host=f"192.168.1.{sort_order + 10}",
        username="u",
        password_enc=encrypt_secret("p"),
        sort_order=sort_order,
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return cam


def test_move_camera_changes_live_order(auth_client):
    db = db_module.SessionLocal()
    try:
        a = _add_cam(db, "A", 0)
        b = _add_cam(db, "B", 1)
        c = _add_cam(db, "C", 2)
    finally:
        db.close()

    r = auth_client.post(
        f"/cameras/{b.id}/move",
        data={"direction": "up"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    db = db_module.SessionLocal()
    try:
        ordered = [cam.name for cam in db.query(Camera).order_by(Camera.sort_order, Camera.id).all()]
        assert ordered == ["B", "A", "C"]
        assert {a.id, b.id, c.id} == {cam.id for cam in db.query(Camera).all()}
    finally:
        db.close()
