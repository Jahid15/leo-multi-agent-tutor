import argparse
import os

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from leo.config import check_api_key  # noqa: E402
from leo.orchestrator import LeoSession  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Leo, a multi-agent AI tutor")
    parser.add_argument("--verbose", action="store_true", help="show CrewAI internal agent logs")
    args = parser.parse_args()
    check_api_key()
    LeoSession(verbose=args.verbose).start()


if __name__ == "__main__":
    main()
