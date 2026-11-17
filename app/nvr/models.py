from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nvr.db import Base

ROLE_SUPERADMIN = "superadmin"
ROLE_USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=ROLE_USER, index=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)

    sites: Mapped[list["Site"]] = relationship(back_populates="owner")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lan_cidr: Mapped[str] = mapped_column(String(64), default="192.168.1.0/24")
    vpn_cidr: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tunnel_ip: Mapped[str] = mapped_column(String(64), unique=True)
    wg_public_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wg_private_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    lan_iface: Mapped[str] = mapped_column(String(64), default="eth0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    owner: Mapped["User"] = relationship(back_populates="sites")
    cameras: Mapped[list["Camera"]] = relationship(back_populates="site")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    host: Mapped[str] = mapped_column(String(255))
    host_lan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    username: Mapped[str] = mapped_column(String(64))
    password_enc: Mapped[str] = mapped_column(Text)
    channel: Mapped[int] = mapped_column(Integer, default=1)
    stream_sub: Mapped[int] = mapped_column(Integer, default=1)
    stream_main: Mapped[int] = mapped_column(Integer, default=0)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    dashboard_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reachability_status: Mapped[str] = mapped_column(String(32), default="ok")
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    site: Mapped["Site | None"] = relationship(back_populates="cameras")
    segments: Mapped[list["RecordingSegment"]] = relationship(
        back_populates="camera",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="camera",
        cascade="all, delete-orphan",
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    camera: Mapped[Camera] = relationship(back_populates="notifications")


class HealthLog(Base):
    __tablename__ = "health_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    event: Mapped[str] = mapped_column(String(32), default="ping")
    camera_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    camera_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text)


class RecordingSegment(Base):
    __tablename__ = "recording_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    path: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    camera: Mapped[Camera] = relationship(back_populates="segments")
