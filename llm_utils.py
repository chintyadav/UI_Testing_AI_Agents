"""
llm_utils.py
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
───────
Shared utilities for LLM call resilience across all agents.

Provides a wrapper function that handles Groq rate limits gracefully by:
  1. Catching 429 RateLimitError
  2. Extracting suggested wait time from error message
  3. Waiting the recommended duration (with buffer)
  4. Automatically retrying the request

This prevents the entire pipeline from crashing when daily token limits are hit.

PUBLIC API
──────────
    from llm_utils import invoke_llm_with_retry

    response_text = invoke_llm_with_retry(llm_client, prompt, max_retries=3)
"""

import time
import re


def invoke_llm_with_retry(llm_client, prompt: str, max_retries: int = 3) -> str:
    """
    Invoke LLM with automatic retry on rate limit (429) errors.

    If Groq rate limits the request, waits the recommended time and retries.
    This gracefully handles daily token limits without crashing the pipeline.

    Args:
        llm_client: The LangChain ChatGroq client instance
        prompt: The prompt to send to the LLM
        max_retries: How many times to retry on rate limit (default 3)

    Returns:
        The LLM response text (stripped)

    Raises:
        Exception: If max retries exhausted or other fatal error occurs
    """
    from groq import RateLimitError

    for attempt in range(max_retries):
        try:
            resp = llm_client.invoke(prompt)
            return resp.content.strip()
        except RateLimitError as e:
            # Extract wait time from error message if available
            error_msg = str(e)
            wait_seconds = 65  # default wait — safer to wait longer

            # Try to extract actual wait time from message
            # Format: "Please try again in 5m11.039999999s"
            if "Please try again in" in error_msg:
                match = re.search(r"Please try again in ([\d.]+)s", error_msg)
                if match:
                    wait_seconds = int(float(match.group(1))) + 10  # add 10s buffer

            if attempt == max_retries - 1:
                print(f"\n  ❌  Rate limit exceeded after {max_retries} retries")
                print(f"      See: https://console.groq.com/settings/billing to upgrade")
                raise

            print(f"\n  ⏳  Groq rate limit (429) — waiting {wait_seconds}s before retry {attempt + 1}/{max_retries}...")
            print(f"      Daily token limit reached. See output above for usage details.")
            print(f"      To avoid this: upgrade plan at https://console.groq.com/settings/billing")

            time.sleep(wait_seconds)
            print(f"  ▶  Retrying LLM request...")

    raise Exception("Unreachable: max_retries exhausted")
