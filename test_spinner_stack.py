import time
import sys
import os
import threading

# Add src to path
sys.path.append(os.path.abspath("src"))

from jules_agent.spinner import spinner, set_status_callback

def test_nested_spinner():
    captured_messages = []

    def mock_callback(msg):
        if msg:
            # We only care about the message part, not the braille character
            captured_messages.append(msg[2:])
        else:
            captured_messages.append("CLEARED")

    set_status_callback(mock_callback)

    print("Starting outer spinner...")
    with spinner("Outer", interval=0.05):
        time.sleep(0.2)
        print("Starting inner spinner...")
        with spinner("Inner", interval=0.05):
            time.sleep(0.2)
        print("Inner spinner finished.")
        time.sleep(0.2)
    print("Outer spinner finished.")
    time.sleep(0.1)

    print(f"Captured: {captured_messages}")

    seen_outer = any("Outer" in m for m in captured_messages)
    seen_inner = any("Inner" in m for m in captured_messages)

    assert seen_outer, "Outer message not seen"
    assert seen_inner, "Inner message not seen"

    # Verify that Inner appears after some Outers, and then Outer appears again
    inner_indices = [i for i, m in enumerate(captured_messages) if "Inner" in m]
    last_inner_idx = inner_indices[-1]

    after_inner = captured_messages[last_inner_idx + 1:]
    restored_outer = any("Outer" in m for m in after_inner)
    cleared = "CLEARED" in captured_messages

    assert restored_outer, "Outer message not restored after Inner finished"
    assert cleared, "Final status not cleared"
    print("Test passed!")

if __name__ == "__main__":
    test_nested_spinner()
