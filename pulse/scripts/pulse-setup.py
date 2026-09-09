#!/usr/bin/env python3
"""Pulse Setup Wizard

Interactive setup tool that generates configuration and cron templates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

# Import shared utilities
sys.path.insert(0, str(Path(__file__).parent))
from pulse_config import PulseConfig, load_config, atomic_write


def print_header() -> None:
    """Print setup wizard header."""
    print("=" * 60)
    print("🫀 Pulse Scanner Setup Wizard")
    print("=" * 60)
    print()


def print_section(title: str) -> None:
    """Print section separator."""
    print()
    print(f"--- {title} ---")
    print()


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for yes/no answer."""
    default_str = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{default_str}]: ").strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print("Please answer 'y' or 'n'")


def prompt_string(question: str, default: Optional[str] = None) -> str:
    """Prompt for string input."""
    default_str = f" [{default}]" if default else ""
    while True:
        answer = input(f"{question}{default_str}: ").strip()
        if answer:
            return answer
        if default:
            return default
        print("Please provide a value")


def prompt_int(question: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Prompt for integer input with validation."""
    while True:
        answer = input(f"{question} [{default}]: ").strip()
        if not answer:
            return default
        
        try:
            value = int(answer)
            if min_val is not None and value < min_val:
                print(f"Value must be >= {min_val}")
                continue
            if max_val is not None and value > max_val:
                print(f"Value must be <= {max_val}")
                continue
            return value
        except ValueError:
            print("Please enter a valid integer")


def select_signal_source() -> str:
    """Interactive signal source selection."""
    print("Available signal sources:")
    print("1. leaderboard_get_smart_money_token_inflows (recommended)")
    print("2. leaderboard_get_trader_performance")
    print("3. leaderboard_get_top_traders")
    print("4. market_get_trending_tokens")
    print("5. Custom script path")
    
    while True:
        choice = input("Select signal source [1]: ").strip()
        if not choice:
            choice = "1"
        
        if choice == "1":
            return "leaderboard_get_smart_money_token_inflows"
        elif choice == "2":
            return "leaderboard_get_trader_performance"
        elif choice == "3":
            return "leaderboard_get_top_traders"
        elif choice == "4":
            return "market_get_trending_tokens"
        elif choice == "5":
            return prompt_string("Enter custom script path")
        else:
            print("Please select 1-5")


def configure_escalation_action() -> Tuple[str, Optional[str], Optional[str]]:
    """Configure escalation action."""
    print("Escalation actions:")
    print("1. alert - Send notification")
    print("2. script - Run custom script")
    print("3. wake_main - Wake main session")
    print("4. none - No action")
    
    while True:
        choice = input("Select escalation action [1]: ").strip()
        if not choice:
            choice = "1"
        
        if choice == "1":
            alert_target = prompt_string("Alert target (e.g. telegram:123456)", "")
            return "alert", None, alert_target if alert_target else None
        elif choice == "2":
            script_path = prompt_string("Escalation script path")
            return "script", script_path, None
        elif choice == "3":
            return "wake_main", None, None
        elif choice == "4":
            return "none", None, None
        else:
            print("Please select 1-4")


def create_interactive_config() -> Dict[str, Any]:
    """Create configuration through interactive prompts."""
    config = {}
    
    print_section("Basic Configuration")
    
    # Instance configuration
    instance_id = prompt_string("Instance ID (for multiple scanners)", "default")
    config["instance_id"] = instance_id
    
    # Signal source
    config["signal_source"] = select_signal_source()
    
    # State directory
    state_dir = prompt_string("State directory", "/data/workspace/state/pulse")
    config["state_dir"] = state_dir
    
    print_section("Heat Thresholds")
    
    # Heat thresholds
    config["warm_threshold"] = prompt_int("Warm threshold (signal count)", 2, 1, 20)
    config["hot_threshold"] = prompt_int("Hot threshold (signal count)", 5, config["warm_threshold"], 50)
    config["decay_threshold"] = prompt_int("Decay threshold (empty cycles)", 3, 1, 10)
    config["hot_persistence"] = prompt_int("Hot persistence (cycles)", 5, 1, 20)
    
    print_section("Polling Intervals")
    
    # Intervals
    config["cold_interval_ms"] = prompt_int("Cold interval (minutes)", 5, 1, 60) * 60 * 1000
    config["warm_interval_ms"] = prompt_int("Warm interval (minutes)", 3, 1, 30) * 60 * 1000
    config["hot_interval_ms"] = prompt_int("Hot interval (seconds)", 90, 30, 300) * 1000
    
    print_section("Signal Filtering")
    
    # Filtering
    config["min_traders"] = prompt_int("Minimum trader count", 10, 1, 100)
    config["min_velocity"] = prompt_int("Minimum velocity", 1, 0, 50)
    config["max_price_change_4h"] = prompt_int("Max 4h price change (%)", 50, 1, 200)
    
    print_section("Escalation Actions")
    
    # Escalation
    action, script, target = configure_escalation_action()
    config["escalation_action"] = action
    if script:
        config["escalation_script"] = script
    if target:
        config["alert_target"] = target
    
    return config


def generate_config_file(config: Dict[str, Any], config_path: str) -> None:
    """Generate and save configuration file."""
    print_section("Generating Configuration")
    
    try:
        atomic_write(config_path, config)
        print(f"✅ Configuration saved to: {config_path}")
    except Exception as e:
        print(f"❌ Failed to save configuration: {e}")
        sys.exit(1)


def generate_cron_templates(config: Dict[str, Any], config_path: str) -> None:
    """Generate cron templates for OpenClaw."""
    print_section("Cron Templates")
    
    # Determine base interval for cron (use cold interval)
    interval_ms = config.get("cold_interval_ms", 300000)
    interval_minutes = max(1, interval_ms // (60 * 1000))  # Minimum 1 minute
    
    scanner_script = str(Path(__file__).parent / "pulse-scanner.py")
    config_arg = f"--config-path {config_path}" if config_path != "pulse-config.json" else ""
    
    print(f"Add these cron jobs to OpenClaw scheduler (updates every {interval_minutes} min):")
    print()
    
    # Main session template
    print("🔹 MAIN SESSION (system events):")
    print("openclaw cron add \\")
    print(f'  "Pulse Scanner ({config.get("instance_id", "default")})" \\')
    print(f"  --schedule \"*/{interval_minutes} * * * *\" \\")
    print("  --session-target main \\")
    print(f"  --payload '{{\"kind\":\"systemEvent\",\"text\":\"python3 {scanner_script} {config_arg}\"}}' \\")
    print("  --wake-mode now")
    print()
    
    # Isolated session template
    print("🔹 ISOLATED SESSION (dedicated agent):")
    print("openclaw cron add \\")
    print(f'  "Pulse Scanner ({config.get("instance_id", "default")})" \\')
    print(f"  --schedule \"*/{interval_minutes} * * * *\" \\")
    print("  --session-target isolated \\")
    print(f"  --payload '{{\"kind\":\"agentTurn\",\"text\":\"Run pulse scanner: {scanner_script} {config_arg}\"}}' \\")
    print("  --wake-mode now")
    print()


def main() -> int:
    """Main setup logic."""
    parser = argparse.ArgumentParser(description="Pulse scanner setup wizard")
    parser.add_argument("--config-path", default="pulse-config.json", help="Config file path")
    parser.add_argument("--state-dir", help="Override state directory")
    parser.add_argument("--mid-model", action="store_true", help="Configure for mid-tier model")
    parser.add_argument("--budget-model", action="store_true", help="Configure for budget model")
    parser.add_argument("--non-interactive", action="store_true", help="Generate defaults without prompts")
    
    args = parser.parse_args()
    
    try:
        if not args.non_interactive:
            print_header()
            
            if prompt_yes_no("Run interactive setup wizard?", True):
                config = create_interactive_config()
            else:
                print("Using default configuration...")
                config = {}
        else:
            config = {}
        
        # Apply CLI overrides
        if args.state_dir:
            config["state_dir"] = args.state_dir
        
        # Model-specific adjustments
        if args.budget_model:
            # More conservative thresholds for budget model
            config.setdefault("warm_threshold", 1)
            config.setdefault("hot_threshold", 3)
            config.setdefault("min_traders", 5)
        elif args.mid_model:
            # Balanced thresholds for mid-tier model
            config.setdefault("warm_threshold", 2)
            config.setdefault("hot_threshold", 4)
            config.setdefault("min_traders", 8)
        
        # Generate configuration file
        generate_config_file(config, args.config_path)
        
        # Generate cron templates
        generate_cron_templates(config, args.config_path)
        
        print_section("Setup Complete")
        print("✅ Pulse scanner is ready!")
        print()
        print("Next steps:")
        print("1. Test the scanner:")
        print(f"   python3 {Path(__file__).parent}/pulse-scanner.py --config-path {args.config_path} --dry-run")
        print("2. Add a cron job using one of the templates above")
        print("3. Monitor the state file and logs")
        print()
        
        return 0
    
    except KeyboardInterrupt:
        print("\n\n❌ Setup cancelled by user")
        return 1
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())