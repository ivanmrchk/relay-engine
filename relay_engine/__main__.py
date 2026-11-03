import sys

from dotenv import load_dotenv

from relay_engine.cli import main

# Only when run as a program: tests call main() directly and never pick up a real .env.
# Variables already set (by Fly or n8n) win over the file.
load_dotenv()
sys.exit(main())
