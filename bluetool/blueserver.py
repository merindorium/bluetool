# Bluetool code is placed under the GPL license.
# Written by Aleksandr Aleksandrov (aleksandr.aleksandrov@emlid.com)
# Copyright (c) 2016, Emlid Limited
# All rights reserved.

# If you are interested in using Bluetool code as a part of a
# closed source project, please contact Emlid Limited (info@emlid.com).

# This file is part of Bluetool.

# Bluetool is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# Bluetool is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with Bluetool.  If not, see <http://www.gnu.org/licenses/>.

import socket
import select
import multiprocessing
import dbus
import dbus.service
import dbus.mainloop.glib
try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject
from daemon_tree.stream_socket import StreamSvr, StreamCli, SocketError
from daemon_tree.bridge import IO, DuplexBridge, BridgeIOError
from bluetool import Bluetooth
from utils import print_error


class SerialPort(object):

    profile_path = "/org/bluez/myprofile"

    def __init__(self, channel=1):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.uuid = "1101"
        self.opts = {
            "Name": "Reach SPP",
            "Channel": dbus.UInt16(channel),
            "AutoConnect": False
        }
        self.manager = dbus.Interface(
            self.bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1"
        )

    def initialize(self):
        try:
            self.manager.RegisterProfile(
                self.profile_path, self.uuid, self.opts)
        except dbus.exceptions.DBusException as error:
            print_error(error)
            return False

        return True

    def deinitialize(self):
        try:
            self.manager.UnregisterProfile(self.profile_path)
        except dbus.exceptions.DBusException:
            pass


class BluetoothServer(dbus.service.Object):

    def __init__(
            self, tcp_port=8043, channel=1,
            tcp_buffer_size=1024, blue_buffer_size=1024):
        self.spp = SerialPort(channel)
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.service.Object.__init__(
            self, dbus.SystemBus(), self.spp.profile_path)
        self.tcp_port = tcp_port
        self.tcp_buffer_size = tcp_buffer_size
        self.blue_buffer_size = blue_buffer_size
        self.server_process = None
        self.mainloop = GObject.MainLoop()

    def run(self):
        if not self.spp.initialize():
            return
        self.mainloop.run()

    def run_in_background(self):
        if not self.spp.initialize():
            return False

        if self.server_process is None:
            self.server_process = multiprocessing.Process(
                target=self.mainloop.run)
            self.server_process.start()

        return True

    def quit(self):
        self.mainloop.quit()

        if self.server_process is not None:
            self.server_process.terminate()
            self.server_process.join()
            self.server_process = None

        self.spp.deinitialize()

    @dbus.service.method(
        "org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, properties):
        address = str(path)
        address = address[len(address) - 17:len(address)]
        address = address.replace("_", ":")

        print_error("Connected:", address)

        tcp_svr = StreamSvr("tcp://localhost:{}".format(self.tcp_port))

        try:
            tcp_svr.initialize()
            tcp_cli, _ = tcp_svr.accept()
        except SocketError as error:
            print_error(error)
            tcp_svr.close()
            _disconnect_device(address)
            return

        blue_sock = socket.fromfd(
            fd.take(), socket.AF_UNIX, socket.SOCK_STREAM)
        blue_sock.setblocking(1)

        io1 = IO(
            tcp_cli, write=tcp_cli.send, read=tcp_cli.recv,
            read_buffer=self.tcp_buffer_size)
        io2 = IO(
            blue_sock, write=blue_sock.send, read=blue_sock.recv,
            read_buffer=self.blue_buffer_size)

        bridge = DuplexBridge(input1=io1, input2=io2)

        try:
            bridge.run()
        except BridgeIOError as error:
            print_error(error)

        tcp_cli.close()
        tcp_svr.close()
        blue_sock.close()
        _disconnect_device(address)


def _disconnect_device(address):
    Bluetooth().disconnect(address)
