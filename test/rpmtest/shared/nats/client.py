"""Small protocol test. See https://docs.nats.io/reference/protocols/client."""

import json
import socket
import time
from contextlib import ExitStack


def connect(scope):
    deadline = time.monotonic() + 20
    while True:
        try:
            connection = socket.create_connection(("subject", 4222), timeout=2)
            break
        except (ConnectionRefusedError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    scope.enter_context(connection)
    stream = scope.enter_context(connection.makefile("rwb", buffering=0))
    info = line(stream)
    assert info.startswith(b"INFO "), info
    assert json.loads(info[5:])["jetstream"] is True
    stream.write(b'CONNECT {"verbose":false,"pedantic":true}\r\n')
    return stream


def line(stream):
    while True:
        response = stream.readline()
        assert response, "Server disconnected"
        if response == b"PING\r\n":
            stream.write(b"PONG\r\n")
        else:
            return response


def subscribe(stream, topic, sid):
    stream.write(b"SUB " + topic + b" " + sid + b"\r\nPING\r\n")
    assert line(stream) == b"PONG\r\n"


def publish(stream, topic, payload, reply=None):
    address = topic if reply is None else topic + b" " + reply
    stream.write(b"PUB " + address + b" " + str(len(payload)).encode() + b"\r\n")
    stream.write(payload + b"\r\nPING\r\n")
    assert line(stream) == b"PONG\r\n"


def receive(stream, topic, sid, payload, reply=None):
    header = [b"MSG", topic, sid]
    if reply is not None:
        header.append(reply)
    header.append(str(len(payload)).encode())
    assert line(stream).split() == header
    # Buffered reads can return short data, so consume the frame explicitly.
    remaining = len(payload) + 2
    chunks = []
    while remaining:
        chunk = stream.read(remaining)
        assert chunk, "Truncated payload"
        chunks.append(chunk)
        remaining -= len(chunk)
    assert b"".join(chunks) == payload + b"\r\n"


with ExitStack() as scope:
    subscriber = connect(scope)
    publisher = connect(scope)
    subscribe(subscriber, b"rpmtest.events.*", b"1")
    for payload in (b"hello NATS", b"", b"\x00binary\r\npayload\xff"):
        publish(publisher, b"rpmtest.events.one", payload)
        receive(subscriber, b"rpmtest.events.one", b"1", payload)
    subscribe(subscriber, b"rpmtest.request", b"2")
    subscribe(publisher, b"_INBOX.rpmtest", b"3")
    publish(publisher, b"rpmtest.request", b"question", reply=b"_INBOX.rpmtest")
    receive(subscriber, b"rpmtest.request", b"2", b"question", reply=b"_INBOX.rpmtest")
    publish(subscriber, b"_INBOX.rpmtest", b"answer")
    receive(publisher, b"_INBOX.rpmtest", b"3", b"answer")
    print("publish/subscribe and request/reply passed")
