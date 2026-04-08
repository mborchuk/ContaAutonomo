#!/usr/bin/env python3
"""
Currency converter: USD to EUR using European Central Bank exchange rates.
Converts salary amounts using rates from a specified date or the last day of previous month.
"""

import requests
from datetime import datetime, timedelta
import sys


# Comprehensive currency code → symbol mapping.
# Used across the app for display formatting.
# Fallback: if a code is not here, the code itself is used (e.g. "XOF").
CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': '¥',
    'CHF': 'Fr', 'CAD': 'C$', 'AUD': 'A$', 'NZD': 'NZ$', 'HKD': 'HK$',
    'SGD': 'S$', 'SEK': 'kr', 'NOK': 'kr', 'DKK': 'kr', 'ISK': 'kr',
    'CZK': 'Kč', 'PLN': 'zł', 'HUF': 'Ft', 'RON': 'lei', 'BGN': 'лв',
    'HRK': 'kn', 'UAH': '₴', 'RUB': '₽', 'TRY': '₺', 'BRL': 'R$',
    'MXN': 'Mex$', 'ARS': 'AR$', 'CLP': 'CL$', 'COP': 'COL$',
    'INR': '₹', 'KRW': '₩', 'THB': '฿', 'IDR': 'Rp', 'MYR': 'RM',
    'PHP': '₱', 'VND': '₫', 'ZAR': 'R', 'EGP': 'E£', 'NGN': '₦',
    'KES': 'KSh', 'GHS': 'GH₵', 'ILS': '₪', 'SAR': '﷼', 'AED': 'د.إ',
    'QAR': 'QR', 'KWD': 'د.ك', 'BHD': 'BD', 'OMR': 'ر.ع.',
    'TWD': 'NT$', 'PKR': '₨', 'LKR': '₨', 'BDT': '৳',
}


def get_currency_symbol(code):
    """Return the symbol for a currency code, or the code itself as fallback."""
    return CURRENCY_SYMBOLS.get(code, code)


def get_last_day_previous_month():
    """Get the last day of the previous month."""
    today = datetime.now()
    first_day_current_month = today.replace(day=1)
    last_day_previous_month = first_day_current_month - timedelta(days=1)
    return last_day_previous_month.strftime('%Y-%m-%d')


def get_exchange_rate_ecb(date_str):
    """
    Fetch USD to EUR exchange rate from European Central Bank (primary method).
    Uses the ECB's public XML feed. If exact date not found, uses nearest earlier date.

    Args:
        date_str: Date in format 'YYYY-MM-DD'

    Returns:
        Tuple of (exchange rate as float, actual date used) or (None, None) if failed
    """
    try:
        # ECB publishes daily rates in XML format
        url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        # Parse XML to find the rate for the specific date
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)

        # ECB XML namespace
        ns = {'gesmes': 'http://www.gesmes.org/xml/2002-09-01',
              'xmlns': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'}

        target_date = datetime.strptime(date_str, '%Y-%m-%d')

        # Collect all available dates with USD rates
        available_rates = []
        for day_cube in root.findall('.//xmlns:Cube[@time]', ns):
            cube_date_str = day_cube.get('time')
            cube_date = datetime.strptime(cube_date_str, '%Y-%m-%d')

            # Find USD rate for this date
            for currency_cube in day_cube.findall('xmlns:Cube[@currency="USD"]', ns):
                rate = float(currency_cube.get('rate'))
                available_rates.append((cube_date, cube_date_str, rate))

        if not available_rates:
            return None, None

        # Sort by date (most recent first)
        available_rates.sort(reverse=True)

        # Find exact match or nearest earlier date
        for cube_date, cube_date_str, rate in available_rates:
            if cube_date <= target_date:
                # ECB gives EUR to USD, we need USD to EUR
                return 1 / rate, cube_date_str

        # If requested date is before all available dates, use oldest available
        oldest_date, oldest_date_str, oldest_rate = available_rates[-1]
        return 1 / oldest_rate, oldest_date_str

    except Exception as e:
        print(f"ECB method failed: {e}")
        return None, None


def get_exchange_rate_exchangerate_api(date_str):
    """
    Fetch USD to EUR exchange rate from exchangerate-api.com (fallback method).

    Args:
        date_str: Date in format 'YYYY-MM-DD'

    Returns:
        Exchange rate as float or None if failed
    """
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/USD"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        rate = data.get('rates', {}).get('EUR')
        if rate:
            return float(rate)

        return None

    except Exception as e:
        print(f"Exchangerate-api method failed: {e}")
        return None


def get_exchange_rate(date_str):
    """
    Fetch USD to EUR exchange rate, trying multiple sources.

    Args:
        date_str: Date in format 'YYYY-MM-DD'

    Returns:
        Tuple of (exchange rate as float, actual date used as string)
    """
    # Try ECB first (official source)
    rate, actual_date = get_exchange_rate_ecb(date_str)
    if rate:
        if actual_date != date_str:
            print(f"✓ Using European Central Bank rate from {actual_date} (nearest available date)")
        else:
            print(f"✓ Using European Central Bank rate")
        return rate, actual_date

    # Fallback to exchangerate-api (uses latest rate, not historical)
    print(f"⚠ ECB data not available for {date_str}, trying alternative source...")
    rate = get_exchange_rate_exchangerate_api(date_str)
    if rate:
        print(f"✓ Using current exchange rate (historical rate not available)")
        return rate, "current"

    print("Error: Could not fetch exchange rate from any source")
    return 1.0, "fallback"


def get_multiple_exchange_rates(date_str, currencies, base_currency='EUR'):
    """
    Fetch exchange rates for multiple currencies with specified base currency using ECB.

    Args:
        date_str: Date in format 'YYYY-MM-DD'
        currencies: List of currency codes (e.g., ['USD', 'GBP', 'CZK'])
        base_currency: Base currency for rates (default: 'EUR')

    Returns:
        Dictionary with currency codes as keys and exchange rates as values
    """
    rates = {}

    try:
        # Use European Central Bank as primary source
        url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)

        ns = {'gesmes': 'http://www.gesmes.org/xml/2002-09-01',
              'xmlns': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'}

        target_date = datetime.strptime(date_str, '%Y-%m-%d')

        # Collect all available dates with rates
        available_data = []
        for day_cube in root.findall('.//xmlns:Cube[@time]', ns):
            cube_date_str = day_cube.get('time')
            cube_date = datetime.strptime(cube_date_str, '%Y-%m-%d')

            # Get all currency rates for this date
            day_rates = {}
            for currency_cube in day_cube.findall('xmlns:Cube[@currency]', ns):
                currency = currency_cube.get('currency')
                rate = float(currency_cube.get('rate'))
                day_rates[currency] = rate

            if day_rates:
                available_data.append((cube_date, cube_date_str, day_rates))

        if not available_data:
            raise Exception("No ECB data available")

        # Sort by date (most recent first)
        available_data.sort(reverse=True)

        # Find exact match or nearest earlier date
        selected_rates = None
        for cube_date, cube_date_str, day_rates in available_data:
            if cube_date <= target_date:
                selected_rates = day_rates
                break

        # If requested date is before all available dates, use oldest available
        if not selected_rates:
            selected_rates = available_data[-1][2]

        # ECB uses EUR as base, so rates are EUR to other currencies
        # If base_currency is EUR, use rates directly
        if base_currency == 'EUR':
            for currency in currencies:
                if currency == 'EUR':
                    rates[currency] = 1.0
                elif currency in selected_rates:
                    rates[currency] = selected_rates[currency]
                else:
                    rates[currency] = 0.0
        else:
            # If base_currency is not EUR, need to convert
            # Rate from base to target = (EUR to target) / (EUR to base)
            if base_currency not in selected_rates:
                raise Exception(f"Base currency {base_currency} not found in ECB data")

            base_rate = selected_rates[base_currency]
            for currency in currencies:
                if currency == base_currency:
                    rates[currency] = 1.0
                elif currency == 'EUR':
                    rates[currency] = 1.0 / base_rate
                elif currency in selected_rates:
                    rates[currency] = selected_rates[currency] / base_rate
                else:
                    rates[currency] = 0.0

        return rates

    except Exception as e:
        print(f"ECB method failed for multiple rates: {e}")
        # Fallback to exchangerate-api
        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{base_currency}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            api_rates = data.get('rates', {})

            for currency in currencies:
                if currency == base_currency:
                    rates[currency] = 1.0
                elif currency in api_rates:
                    rates[currency] = float(api_rates[currency])
                else:
                    rates[currency] = 0.0

            return rates

        except Exception as e2:
            print(f"Fallback method also failed: {e2}")
            # Return default rates
            return {currency: 1.0 if currency == base_currency else 0.0 for currency in currencies}


def convert_usd_to_eur(amount_usd, exchange_rate):
    """Convert USD amount to EUR using the exchange rate."""
    return amount_usd * exchange_rate


def main():
    print("USD to EUR Salary Converter")
    print("=" * 40)

    # Get salary amount
    try:
        amount_str = input("Enter salary amount in USD: $")
        amount_usd = float(amount_str.replace(',', ''))
    except ValueError:
        print("Error: Invalid amount entered")
        sys.exit(1)

    # Get date (optional)
    date_input = input("Enter date (YYYY-MM-DD) or press Enter for last day of previous month: ").strip()

    if date_input:
        try:
            # Validate date format
            datetime.strptime(date_input, '%Y-%m-%d')
            date_str = date_input
        except ValueError:
            print("Error: Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        date_str = get_last_day_previous_month()
        print(f"Using date: {date_str} (last day of previous month)")

    # Fetch exchange rate
    print(f"\nFetching exchange rate from European Central Bank for {date_str}...")
    exchange_rate, actual_date = get_exchange_rate(date_str)

    # Convert
    amount_eur = convert_usd_to_eur(amount_usd, exchange_rate)

    # Display results
    print("\n" + "=" * 40)
    print(f"Requested Date: {date_str}")
    if actual_date != date_str and actual_date != "current":
        print(f"Actual Date Used: {actual_date}")
    print(f"Exchange Rate (USD to EUR): {exchange_rate:.4f}")
    print(f"Amount in USD: ${amount_usd:,.2f}")
    print(f"Amount in EUR: €{amount_eur:,.2f}")
    print("=" * 40)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Additional currency providers — can be registered with CurrencyService
# ---------------------------------------------------------------------------

def make_frankfurter_provider():
    """
    Frankfurter (api.frankfurter.app) — free, no API key, ECB data.
    Returns a provider function compatible with CurrencyService.register_provider().
    """
    def provider(from_currency, to_currency, date_str):
        try:
            url = f"https://api.frankfurter.app/{date_str}"
            resp = requests.get(url, params={'from': from_currency, 'to': to_currency}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rate = data.get('rates', {}).get(to_currency)
            actual_date = data.get('date', date_str)
            if rate:
                return float(rate), actual_date
        except Exception as e:
            print(f"Frankfurter provider failed: {e}")
        return None, None
    return provider


def make_open_exchange_rates_provider(api_key):
    """
    Open Exchange Rates (openexchangerates.org) — free tier 1000 req/month, USD base.
    Requires API key. Returns a provider function.
    """
    def provider(from_currency, to_currency, date_str):
        try:
            url = f"https://openexchangerates.org/api/historical/{date_str}.json"
            resp = requests.get(url, params={'app_id': api_key, 'base': 'USD'}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get('rates', {})
            # Convert: from_currency -> USD -> to_currency
            if from_currency == 'USD':
                rate = rates.get(to_currency)
            elif to_currency == 'USD':
                from_rate = rates.get(from_currency)
                rate = 1.0 / from_rate if from_rate else None
            else:
                from_rate = rates.get(from_currency)
                to_rate = rates.get(to_currency)
                rate = (to_rate / from_rate) if from_rate and to_rate else None
            if rate:
                return float(rate), date_str
        except Exception as e:
            print(f"Open Exchange Rates provider failed: {e}")
        return None, None
    return provider


def make_fixer_provider(api_key):
    """
    Fixer.io — free tier 100 req/month, EUR base.
    Requires API key. Returns a provider function.
    """
    def provider(from_currency, to_currency, date_str):
        try:
            url = f"https://data.fixer.io/api/{date_str}"
            resp = requests.get(url, params={'access_key': api_key, 'base': 'EUR',
                                             'symbols': f'{from_currency},{to_currency}'}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not data.get('success'):
                return None, None
            rates = data.get('rates', {})
            if from_currency == 'EUR':
                rate = rates.get(to_currency)
            elif to_currency == 'EUR':
                from_rate = rates.get(from_currency)
                rate = 1.0 / from_rate if from_rate else None
            else:
                from_rate = rates.get(from_currency)
                to_rate = rates.get(to_currency)
                rate = (to_rate / from_rate) if from_rate and to_rate else None
            actual_date = data.get('date', date_str)
            if rate:
                return float(rate), actual_date
        except Exception as e:
            print(f"Fixer provider failed: {e}")
        return None, None
    return provider


# Registry of built-in providers (name -> factory or None for default)
BUILTIN_PROVIDERS = {
    'ecb': None,           # Default — no registration needed
    'frankfurter': make_frankfurter_provider,
    'open_exchange_rates': make_open_exchange_rates_provider,
    'fixer': make_fixer_provider,
}

PROVIDER_LABELS = {
    'ecb': 'ECB (European Central Bank) — default',
    'frankfurter': 'Frankfurter — free, no API key',
    'open_exchange_rates': 'Open Exchange Rates — API key required',
    'fixer': 'Fixer.io — API key required',
}
