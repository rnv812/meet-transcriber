"""Тесты не ходят в сеть: общий сторож из conftest (локальные адреса можно)."""

import socket
import urllib.request

import pytest


def test_urlopen_to_the_internet_is_refused():
    with pytest.raises(AssertionError, match="тест полез в сеть"):
        urllib.request.urlopen("https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/x.ckpt")


def test_socket_connect_to_the_internet_is_refused():
    with socket.socket() as sock, pytest.raises(AssertionError, match="тест полез в сеть"):
        sock.connect(("huggingface.co", 443))


def test_localhost_still_works():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
