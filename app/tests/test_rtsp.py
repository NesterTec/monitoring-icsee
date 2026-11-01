from nvr.rtsp import build_rtsp_url


def test_build_rtsp_encodes_at():
    url = build_rtsp_url("192.168.1.15", 554, "ahkn", "a@b", 1, 1)
    assert "a%40b" in url
    assert url.startswith("rtsp://ahkn:a%40b@192.168.1.15:554/")
