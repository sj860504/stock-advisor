try:
    import tradingeconomics as te
    te.login('guest:guest')
    # Fetch ISM Services PMI
    data = te.getHistoricalData(country='United States', indicator='ISM Non-Manufacturing PMI')
    print("Data fetched successfully!")
    print(data[:5] if data is not None else "No data")
except Exception as e:
    print("Error:", e)
