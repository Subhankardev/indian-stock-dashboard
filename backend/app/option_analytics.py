def analyze_option_chain(chain):
    data = chain["records"]["data"]
    ce_oi = []
    pe_oi = []
    strikes = []
    for row in data:
        strike = row["strikePrice"]
        ce = row.get("CE")
        pe = row.get("PE")
        if ce and pe:
            strikes.append(strike)
            ce_oi.append(ce["openInterest"])
            pe_oi.append(pe["openInterest"])
    total_ce_oi = sum(ce_oi)
    total_pe_oi = sum(pe_oi)
    pcr = total_pe_oi / total_ce_oi if total_ce_oi != 0 else None
    resistance = strikes[ce_oi.index(max(ce_oi))] if ce_oi else None
    support = strikes[pe_oi.index(max(pe_oi))] if pe_oi else None
    return {
        "PCR": pcr,
        "max_CE_OI_strike": resistance,
        "max_PE_OI_strike": support,
        "total_CE_OI": total_ce_oi,
        "total_PE_OI": total_pe_oi,
        "OI_data": list(zip(strikes, ce_oi, pe_oi))
    }