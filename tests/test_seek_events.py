from threading import Event, Lock

from reframe.ipc import IPC


class Player:
    def command(self, *args):
        return 2.0


def test_seek_generation_cannot_reopen_at_old_cached_playhead():
    ipc = IPC.__new__(IPC)
    ipc.lock = Lock()
    ipc.changed = Event()
    ipc.cache = {"time-pos": 20.0}
    ipc.generation = 0
    ipc.restart_pending = False
    ipc.mpv = Player()
    ipc._reset({"event": "seek"})
    ipc._property("seeking", False)
    assert ipc.snapshot()[0]["seeking"] is True
    ipc._restart({"event": "playback-restart"})
    state, generation = ipc.snapshot()
    assert state["time-pos"] == 2
    assert state["seeking"] is False
    assert generation == 2
