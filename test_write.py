import logging
from core.modbus_client import PumpGuruClient

logging.basicConfig(level=logging.DEBUG)
client = PumpGuruClient()
if client.connect():
    print("Connected.")
    success = client.write_holding_register(3053, 0)
    print(f"Write returned: {success}")
    client.close()
else:
    print("Failed to connect.")
