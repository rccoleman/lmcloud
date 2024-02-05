"""Bluetooth class for La Marzocco machines."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from bleak import BaseBleakScanner, BleakClient, BleakError, BleakScanner, BLEDevice

from .const import (
    AUTH_CHARACTERISTIC,
    BT_MODEL_NAMES,
    SETTINGS_CHARACTERISTIC,
    LaMarzoccoBoilerType,
)
from .exceptions import (
    BluetoothConnectionFailed,
    BluetoothDeviceNotFound,
    ClientNotInitialized,
)

_logger = logging.getLogger(__name__)


class LaMarzoccoBluetoothClient:
    """Class to interact with machine via Bluetooth."""

    def __init__(
        self,
        username: str,
        serial_number: str,
        token: str,
        ble_device: BLEDevice | None = None,
    ) -> None:
        """Initializes a new LaMarzoccoBluetoothClient instance, optionally from a BLEDevice."""
        self._username = username
        self._serial_number = serial_number
        self._token = token
        self._address: str | None = None
        self._client: BleakClient | None = None

        if ble_device:
            self._address = ble_device.address
            self._client = BleakClient(ble_device)

    @classmethod
    async def create(
        cls,
        username: str,
        serial_number: str,
        token: str,
        init_client: bool = True,
        bleak_scanner: BaseBleakScanner | None = None,
    ) -> LaMarzoccoBluetoothClient:
        """Init class by scanning for devices and selecting the first one with a suppoted name."""
        self = cls(username, serial_number, token)
        if bleak_scanner is None:
            async with BleakScanner() as scanner:
                await self._discover_device(scanner)
        else:
            await self._discover_device(bleak_scanner)

        if not self._address:
            # couldn't connect
            raise BluetoothDeviceNotFound("Couldn't find a machine")

        if init_client:
            self._client = BleakClient(self._address)
        return self

    @property
    def address(self) -> str:
        """Return the BT MAC address of the machine."""
        if self._address is None:
            raise ClientNotInitialized("Bluetooth client not initialized")
        return self._address

    @property
    def connected(self) -> bool:
        """Return the connection status."""
        if self._client is None:
            return False
        return self._client.is_connected

    async def new_client_from_ble_device(self, ble_device: BLEDevice) -> None:
        """Initalize a new bleak client from a BLEDevice (for Home Assistant)."""

        self._client = BleakClient(ble_device)

        try:
            await self._client.connect()
            await self._authenticate()
        except (BleakError, TimeoutError) as e:
            raise BluetoothConnectionFailed(
                f"Failed to connect to machine with Bluetooth: {e}"
            ) from e

    async def set_power(self, state: bool) -> None:
        """Power on the machine."""
        mode = "BrewingMode" if state else "StandBy"
        data = {
            "name": "MachineChangeMode",
            "parameter": {
                "mode": mode,
            },
        }
        await self._write_bluetooth_json_message(data)

    async def set_steam(self, state: bool) -> None:
        """Power cycle steam."""
        data = {
            "name": "SettingBoilerEnable",
            "parameter": {
                "identifier": "SteamBoiler",
                "state": state,
            },
        }
        await self._write_bluetooth_json_message(data)

    async def set_temp(self, boiler: LaMarzoccoBoilerType, temperature: int) -> None:
        """Set boiler temperature (in Celsius)"""
        data = {
            "name": "SettingBoilerTarget",
            "parameter": {
                "identifier": boiler,
                "value": temperature,
            },
        }
        await self._write_bluetooth_json_message(data)

    async def _discover_device(self, scanner: BaseBleakScanner) -> None:
        """Find machine based on model name."""
        assert hasattr(scanner, "discover")
        devices = await scanner.discover()
        for d in devices:
            if d.name:
                if d.name.startswith(tuple(BT_MODEL_NAMES)):
                    self._address = d.address

    async def _write_bluetooth_message(
        self, characteristic: str, message: bytes | str
    ) -> None:
        """Connect to machine and write a message."""
        if self._client is None:
            raise ClientNotInitialized("Bluetooth client not initialized")

        if not self._client.is_connected:
            try:
                await self._client.connect()
                await self._authenticate()
            except (BleakError, TimeoutError) as e:
                raise BluetoothConnectionFailed(
                    f"Failed to connect to machine with Bluetooth: {e}"
                ) from e

        # check if message is already bytes string
        if not isinstance(message, bytes):
            message = bytes(message, "utf-8")

        # append trailing zeros to settings message
        if characteristic == SETTINGS_CHARACTERISTIC:
            message += b"\x00"

        _logger.debug("Sending bluetooth message: %s to %s", message, characteristic)

        await self._client.write_gatt_char(characteristic, message)

    async def _write_bluetooth_json_message(
        self,
        data: dict[str, Any],
        characteristic: str = SETTINGS_CHARACTERISTIC,
    ) -> None:
        """Write a json message to the machine."""
        await self._write_bluetooth_message(
            characteristic=characteristic,
            message=json.dumps(data, separators=(",", ":")),
        )

    async def _authenticate(self) -> None:
        """Build authentication string and send it to the machine."""
        user = self._username + ":" + self._serial_number
        user_bytes = user.encode("utf-8")
        token = self._token.encode("utf-8")
        auth_string = base64.b64encode(user_bytes) + b"@" + base64.b64encode(token)
        await self._write_bluetooth_message(
            characteristic=AUTH_CHARACTERISTIC,
            message=auth_string,
        )
