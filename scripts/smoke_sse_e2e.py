"""E2E SSE streaming test - verifies 5 agent ReAct pipeline."""
import httpx
import json
import sys

print("Starting SSE stream request...")
print("=" * 60)

try:
    with httpx.stream(
        "POST",
        "http://127.0.0.1:8000/api/plan/stream",
        json={"destination": "东京", "days": 2, "budget": 800, "interests": "文化"},
        timeout=600,
    ) as resp:
        agent_count = 0
        token_count = 0
        final_content = ""

        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                print("\n[DONE]")
                break
            try:
                ev = json.loads(data)
                etype = ev.get("type", "")

                if etype == "status":
                    msg = ev.get("message", "")
                    print(f"[STATUS] {msg}")

                elif etype == "agent_done":
                    msg = ev.get("message", "")
                    agent_count += 1
                    print(f"[AGENT {agent_count}] {msg}")

                elif etype == "token":
                    token_count += 1
                    if token_count <= 100:
                        print(ev.get("token", ""), end="", flush=True)

                elif etype == "final":
                    final_content = ev.get("content", "")
                    plan_id = ev.get("plan_id", "N/A")
                    print(f"\n[FINAL] length={len(final_content)} plan_id={plan_id}")

                elif etype == "error":
                    msg = ev.get("message", "")
                    print(f"[ERROR] {msg}")

            except json.JSONDecodeError:
                pass

        print()
        print("=" * 60)
        print(f"Agents completed: {agent_count}")
        print(f"Tokens received: {token_count}")
        print(f"Final content length: {len(final_content)}")
        if final_content:
            print(f"First 300 chars:\n{final_content[:300]}")
            print(f"\nLast 200 chars:\n{final_content[-200:]}")

except Exception as e:
    print(f"Exception: {e}")
    sys.exit(1)
