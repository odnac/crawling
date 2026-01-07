# main.py
from config import VIC_URL
from modes.security import check_password
from modes.mode_orderbook import run_vic_orderbook_mode
from modes.mode_print_referenced_price import print_binance_referenced_price_mode
from modes.mm.mode_binance_follow import run_follow_mm_bid, run_follow_mm_ask


def _prompt_mode() -> str:
    print("\n\n\n ⚙️  Select Mode ⚙️\n")
    print("1) Show Order Book (VicEX)")
    print("2) Print Binance-Referenced Price")
    print("3) Run Follow Market Maker (Bid)")
    print("4) Run Follow Market Maker (Ask)")
    print("q) Quit")
    return input("\n👉  Select (1/2/3/4/q): ").strip().lower()


def _prompt_ticker() -> str:
    t = input("👉  Coin ticker (e.g. BTC, ETH): ").strip().upper()
    if t.endswith("USDT"):
        t = t[:-4]
    return t


def _prompt_use_fixed_amount() -> bool:
    """
    Ask user if they want to use fixed amount mode.
    Returns True if user wants fixed amount mode, False otherwise.
    """
    print("\n" + "=" * 50)
    print("💰 Trading Amount Mode Selection")
    print("=" * 50)
    print("1) Percentage Mode (use % of available balance)")
    print("2) Fixed Amount Mode (use fixed USDT amount)")
    print("=" * 50)

    while True:
        choice = input("👉  Select mode (1/2): ").strip()
        if choice == "1":
            print("✅ Percentage mode selected")
            print("=" * 50 + "\n")
            return False
        elif choice == "2":
            print("✅ Fixed amount mode selected")
            return True
        else:
            print("❌ Invalid input. Please enter 1 or 2.")


def _prompt_fixed_amount() -> float:
    """
    Prompt user to input fixed USDT amount for trading.
    Returns the amount as float.
    """
    print("=" * 50)

    while True:
        try:
            amount_str = input("👉  Enter USDT amount to use (e.g., 100): ").strip()
            amount = float(amount_str)

            if amount <= 0:
                print("❌ Amount must be greater than 0. Please try again.")
                continue

            confirm = input(f"\n✅ Use {amount:.2f} USDT? (y/n): ").strip().lower()
            if confirm in ["y", "yes"]:
                print(f"✅ Bot will trade with {amount:.2f} USDT")
                print("=" * 50 + "\n")
                return amount
            else:
                print("Please enter the amount again.\n")

        except ValueError:
            print("❌ Invalid input. Please enter a valid number.")
        except KeyboardInterrupt:
            print("\n\n[!] Operation cancelled by user.")
            raise


def main():
    if not check_password():
        return

    while True:
        try:
            mode = _prompt_mode()
            if mode == "1":
                run_vic_orderbook_mode(VIC_URL)
            elif mode == "2":
                print_binance_referenced_price_mode(VIC_URL)
            elif mode == "3":
                ticker = _prompt_ticker()

                # Ask user about fixed amount mode
                use_fixed = _prompt_use_fixed_amount()
                fixed_amount = None
                if use_fixed:
                    fixed_amount = _prompt_fixed_amount()

                run_follow_mm_bid(VIC_URL, ticker, fixed_amount=fixed_amount)
            elif mode == "4":
                ticker = _prompt_ticker()

                # Ask user about fixed amount mode
                use_fixed = _prompt_use_fixed_amount()
                fixed_amount = None
                if use_fixed:
                    fixed_amount = _prompt_fixed_amount()

                run_follow_mm_ask(VIC_URL, ticker, fixed_amount=fixed_amount)
            elif mode == "q":
                print("Bye 👋...\n\n")
                break
            else:
                print("Invalid input. Please enter 1, 2, 3, 4 or q.")

        except KeyboardInterrupt:
            print("\n[!] Interrupted by user (Ctrl+C). Exiting safely...\n\n")
            break


if __name__ == "__main__":
    main()
