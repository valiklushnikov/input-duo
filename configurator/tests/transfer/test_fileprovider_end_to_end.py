"""Real sender/snapshot loopback through the lazy callback boundary."""

import threading
import time
import sys

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.fileprovider_client import FileProviderServiceClient
from duo_input.transfer.model import decode_manifest
from duo_input.transfer.service import FileTransferService
from test_fileprovider_scheduler import RejectingLink, RaisingLink, _backend, _manifest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS XPC callback bridge"
)


class LoopLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self, receive):
        super().__init__()
        self.receive = receive
        self.sent = []

    def send(self, message):
        self.sent.append((time.monotonic_ns(), message))
        self.receive(message)
        return True


@pytest.mark.parametrize("contents", [b"abc", b"a" * 1_048_576 + b"bc"])
def test_real_snapshot_bytes_are_read_only_after_fetch_and_explicit_pull(
    qapp, tmp_path, contents
):
    source = tmp_path / "source.bin"
    source.write_bytes(contents)
    sender = FileTransferService()
    client = FileProviderServiceClient()
    client.remote = lambda: type(
        "Remote",
        (),
        {"publishGeneration_reply_": lambda self, record, reply: reply(True, None)},
    )()
    armed = []
    backend = FileProviderBackend(
        client,
        object(),
        armed.append,
        url_resolver=lambda item, reply: reply("file:///visible", None),
    )
    backend.on_domain_ready()

    def receive(message):
        if message.type is MessageType.FILE_OFFER:
            epoch = backend.handle_offer(decode_manifest(message.blob))
            backend.authorize(True, epoch)
        else:
            backend.handle_message(message)

    sender_link = LoopLink(receive)
    receiver_link = LoopLink(sender.handle_message)
    sender.attach_link(sender_link)
    sender.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    backend.attach_link(receiver_link)
    generation = sender.offer_local_files([source])
    assert generation in sender.snapshots.transfer_ids
    assert armed == [["file:///visible"]]
    assert receiver_link.sent == []
    fetchContents_ts = time.monotonic_ns()
    opened = []
    client._dispatch_extension_call(
        "open", generation, 0, lambda *args: opened.append(args)
    )
    assert len(opened) == 1
    token, size, error = opened[0]
    assert size == len(contents) and error is None
    assert receiver_link.sent == []
    chunks = []
    while not chunks or not chunks[-1][1]:
        before = len(chunks)
        client._dispatch_extension_call(
            "pull", token, lambda *args: chunks.append(args)
        )
        assert len(chunks) == before + 1
        assert chunks[-1][2] is None
    assert b"".join(chunk for chunk, _, _ in chunks) == contents
    assert [eof for _, eof, _ in chunks] == (
        [True] if len(contents) == 3 else [False, True]
    )
    assert receiver_link.sent[0][0] > fetchContents_ts
    assert receiver_link.sent[0][1].type is MessageType.FILE_READ
    sender.detach_link()


@pytest.mark.parametrize("blob", [b"", b"ab", b"abcd"])
def test_bad_chunk_settles_reply_once_and_releases_exact_fetch(qapp, blob):
    backend, link, _, manifest = _backend(qapp, _manifest(sizes=(3, 5)))
    token, _ = backend.open_fetch(manifest.transfer_id, 0)
    fetch = backend.by_token[token]
    replies = []
    backend.pull_chunk(token, lambda *args: replies.append(args))
    read = link.sent[0]
    wrong = Message(MessageType.FILE_CHUNK, dict(read.header, offset=1), blob)
    backend.handle_message(wrong)
    assert replies == []
    message = Message(MessageType.FILE_CHUNK, dict(read.header), blob)
    backend.handle_message(message)
    backend.handle_message(message)
    assert len(replies) == 1
    assert replies[0][:2] == (None, False)
    assert replies[0][2].code() == 7
    assert replies[0][2].domain() == "com.duoinput.configurator.fileprovider.error"
    assert backend.by_read_id == {}
    assert fetch.state == "failed"


def test_queued_pull_waits_then_completes_and_duplicate_does_not_replace_reply(qapp):
    backend, link, _, manifest = _backend(qapp, _manifest(sizes=(3,) * 5))
    tokens = [backend.open_fetch(manifest.transfer_id, i)[0] for i in range(5)]
    replies, duplicate = [], []
    backend.pull_chunk(tokens[4], lambda *a: replies.append(a))
    backend.pull_chunk(tokens[4], lambda *a: duplicate.append(a))
    assert not link.sent and not replies
    assert len(duplicate) == 1 and duplicate[0][2] is not None
    backend.pull_chunk(tokens[0])
    backend.handle_message(
        Message(MessageType.FILE_CHUNK, dict(link.sent[0].header), b"abc")
    )
    assert len(link.sent) == 2
    backend.handle_message(
        Message(MessageType.FILE_CHUNK, dict(link.sent[1].header), b"abc")
    )
    assert replies == [(b"abc", True, None)]


def test_exported_adapter_retains_reply_until_qt_dispatch(qapp, fp_fake_service):
    client = FileProviderServiceClient()
    fp_fake_service.grant_connection(client)
    called = []
    qt_thread = threading.get_ident()
    client.set_callbacks(
        open_fetch=lambda generation, index, reply: reply("token", 3, None),
        pull_chunk=lambda token, reply: reply(b"abc", True, None),
    )

    def reply(*args):
        called.append((threading.get_ident(), args))

    adapter = fp_fake_service.connection.exported_object
    worker = threading.Thread(
        target=lambda: (
            adapter.openFetch_entryId_reply_("generation", 0, reply),
            adapter.pullChunk_reply_("token", reply),
        )
    )
    worker.start()
    worker.join()
    assert called == []
    qapp.processEvents()
    assert called == [
        (qt_thread, ("token", 3, None)),
        (qt_thread, (b"abc", True, None)),
    ]


def test_exported_selectors_describe_native_reply_blocks():
    from duo_input.transfer.fileprovider_client import _ExtensionCallbackAdapter

    for method, index in [
        (_ExtensionCallbackAdapter.openFetch_entryId_reply_, 4),
        (_ExtensionCallbackAdapter.pullChunk_reply_, 3),
    ]:
        argument = method.__metadata__()["arguments"][index]
        assert argument["type"] == b"@?"
        assert argument["callable"]["retval"]["type"] == b"v"
        assert len(argument["callable"]["arguments"]) == 4


@pytest.mark.parametrize("link_type", [RejectingLink, RaisingLink])
def test_send_failure_settles_pending_reply_once(qapp, link_type):
    backend, _, _, manifest = _backend(qapp, link=link_type())
    token, _ = backend.open_fetch(manifest.transfer_id, 0)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    assert len(replies) == 1 and replies[0][2].code() == 3
    assert backend.by_read_id == {}


def test_file_error_and_cancel_settle_only_their_pending_replies(qapp):
    backend, link, _, manifest = _backend(qapp)
    tokens = [backend.open_fetch(manifest.transfer_id, i)[0] for i in range(2)]
    first, second = [], []
    backend.pull_chunk(tokens[0], lambda *a: first.append(a))
    backend.pull_chunk(tokens[1], lambda *a: second.append(a))
    error = Message(
        MessageType.FILE_ERROR, dict(link.sent[0].header, reason="source_missing"), b""
    )
    backend.handle_message(error)
    backend.handle_message(error)
    assert len(first) == 1 and first[0][2].code() == 1
    assert second == []
    backend.cancel_fetch(tokens[1])
    backend.cancel_fetch(tokens[1])
    assert len(second) == 1 and second[0][2] is not None
    assert backend.by_read_id == {}


def test_empty_file_replies_eof_without_read(qapp):
    backend, link, _, manifest = _backend(qapp, _manifest(sizes=(0,)))
    opened, chunks = [], []
    backend.open_fetch(manifest.transfer_id, 0, lambda *a: opened.append(a))
    backend.pull_chunk(opened[0][0], lambda *a: chunks.append(a))
    assert chunks == [(b"", True, None)]
    assert link.sent == []


def test_malformed_error_reason_still_settles_the_matching_reply(qapp):
    backend, link, _, manifest = _backend(qapp)
    token, _ = backend.open_fetch(manifest.transfer_id, 0)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    backend.handle_message(
        Message(MessageType.FILE_ERROR, dict(link.sent[0].header, reason=[]), b"")
    )
    assert len(replies) == 1 and replies[0][2].code() == 7
    assert backend.by_read_id == {}


def test_dispatch_missing_or_raising_callback_always_settles_once(qapp):
    client = FileProviderServiceClient()
    replies = []
    client._dispatch_extension_call("open", "missing", 0, lambda *a: replies.append(a))
    assert len(replies) == 1 and replies[0][2].code() == 8

    def raising(token, reply):
        reply(b"abc", True, None)
        raise RuntimeError("after reply")

    client.set_callbacks(pull_chunk=raising)
    client._dispatch_extension_call("pull", "token", lambda *a: replies.append(a))
    assert len(replies) == 2 and replies[1] == (b"abc", True, None)
