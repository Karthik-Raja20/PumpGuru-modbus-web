"""
PUMPGURU Web App — entry point.

Usage:
    python run.py                        # connect to real device via config/register_map.py
    python run.py --simulate             # run in demo mode with simulated data (no hardware needed)
    python run.py --port 8080 --interval 3
"""

import argparse
from app import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PUMPGURU Web Monitoring App")
    parser.add_argument("--host", default="0.0.0.0", help="host to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="port to run on (default 5000)")
    parser.add_argument("--interval", type=float, default=5.0, help="Modbus poll interval in seconds")
    parser.add_argument("--simulate", action="store_true", help="run with simulated data instead of real hardware")
    parser.add_argument("--debug", action="store_true", default=True, help="enable Flask debug mode")
    args = parser.parse_args()

    app = create_app(simulate=args.simulate, poll_interval=args.interval)

    print(f"\n{'='*60}")
    print(f"  PUMPGURU Web App starting")
    print(f"  Mode: {'SIMULATION (demo data)' if args.simulate else 'LIVE HARDWARE'}")
    print(f"  URL:  http://localhost:{args.port}")
    print(f"{'='*60}\n")

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=True)
