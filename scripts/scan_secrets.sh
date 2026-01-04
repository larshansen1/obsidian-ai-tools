#!/bin/bash
# Scan for secrets in git history and working directory

set -e

echo "🔍 Running gitleaks security scan..."

# Check if gitleaks is installed
if ! command -v gitleaks &> /dev/null; then
    echo "❌ gitleaks not found. Installing..."

    # Detect OS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install gitleaks
        else
            echo "❌ Homebrew not found. Please install gitleaks manually:"
            echo "   Visit: https://github.com/gitleaks/gitleaks/releases"
            exit 1
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v curl &> /dev/null; then
            curl -s https://raw.githubusercontent.com/gitleaks/gitleaks/master/scripts/install.sh | bash
        else
            echo "❌ curl not found. Please install gitleaks manually:"
            echo "   Visit: https://github.com/gitleaks/gitleaks/releases"
            exit 1
        fi
    else
        echo "❌ Unsupported OS: $OSTYPE"
        exit 1
    fi
fi

# Run gitleaks scan
echo "📊 Scanning repository..."
gitleaks detect \
    --source . \
    --report-path gitleaks-report.json \
    --report-format json \
    --verbose \
    --no-git

# Check results
if [ -f gitleaks-report.json ]; then
    # Parse report
    findings=$(cat gitleaks-report.json | jq '. | length' 2>/dev/null || echo "0")

    if [ "$findings" -gt 0 ]; then
        echo "❌ Found $findings potential secret(s)!"
        echo "📄 Report saved to: gitleaks-report.json"
        echo "🔍 View report:"
        echo "   cat gitleaks-report.json | jq"
        exit 1
    else
        echo "✅ No secrets found!"
        rm -f gitleaks-report.json
        exit 0
    fi
else
    echo "✅ No secrets found!"
    exit 0
fi
