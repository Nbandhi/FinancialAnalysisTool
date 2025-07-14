from flask import Flask, render_template, request, send_file
import pandas as pd
import random
import plotly.graph_objs as go
import plotly.offline as pyo

app = Flask(__name__)

def simulate_iul(test_type, start_age, end_age, annual_premium, death_benefit,
                 average_return, interest_cap, interest_floor, initial_basis,
                 use_1035_exchange, prior_cash_value, prior_basis, allow_mec,
                 withdrawal_age=None, withdrawal_amount=0,
                 premium_payment_years=4):
    guideline_factors = {age: 0.025 + 0.0003 * (age - 35) for age in range(35, 86)}
    corridor_factors = {age: max(1.0 + 0.5 - (age - 35) * 0.01, 1.0) for age in range(35, 86)}

    gpt_factor = 0.20
    required_db_for_gpt = (annual_premium * premium_payment_years) / gpt_factor
    required_db_for_mec = annual_premium * 8
    min_required_db = max(required_db_for_gpt, required_db_for_mec)

    if test_type == "CVAT":
        starting_cv = prior_cash_value if use_1035_exchange else annual_premium
        corridor_ratio = corridor_factors.get(start_age, 1.0)
        min_required_db = max(min_required_db, starting_cv * corridor_ratio)

    cash_value = prior_cash_value if use_1035_exchange else 0
    cost_basis = prior_basis if use_1035_exchange else initial_basis
    age = start_age
    history = []

    for year in range(1, end_age - start_age + 2):
        premium = annual_premium if year <= premium_payment_years else 0

        if test_type == "GPT":
            gpl = death_benefit * guideline_factors.get(age, 0.03)
            gpt_failed = premium > gpl
        else:
            min_death_benefit = cash_value * corridor_factors.get(age, 1.0)
            if cash_value > 0 and death_benefit < min_death_benefit:
                death_benefit = min_death_benefit
            gpt_failed = False

        cash_value += premium
        cost_basis += premium

        coi = death_benefit * (0.005 + 0.0005 * (age - start_age))
        if cash_value < coi:
            history.append({
                "Age": age,
                "Year": year,
                "Premium": premium,
                "COI": coi,
                "Index Credit (%)": 0,
                "Cash Value": 0,
                "Cost Basis": cost_basis,
                "Death Benefit": 0,
                "GPT Failed": gpt_failed,
                "TEFRA Failed": True,
                "MEC Status": True,
                "Withdrawal": 0,
                "Tax on Withdrawal": 0,
                "Penalty (if <59.5)": 0,
                "Death Benefit Tax-Free": False,
                "Policy Lapsed": True
            })
            break

        cash_value -= coi
        raw_return = random.gauss(average_return, 0.01)
        credited_return = min(max(raw_return, interest_floor), interest_cap)
        cash_value += cash_value * (credited_return / 100)

        corridor_limit = death_benefit * corridor_factors.get(age, 1.0)
        tefra_failed = cash_value > corridor_limit
        is_mec = gpt_failed and not allow_mec

        tax = penalty = withdrawal = 0
        if withdrawal_age and age >= withdrawal_age and withdrawal_amount > 0:
            if is_mec:
                taxable = min(max(cash_value - cost_basis, 0), withdrawal_amount)
                withdrawal = withdrawal_amount
                penalty = taxable * 0.10 if age < 59.5 else 0
                tax = taxable * 0.24
            else:
                non_taxable = min(cost_basis, withdrawal_amount)
                taxable = withdrawal_amount - non_taxable
                withdrawal = withdrawal_amount
                cost_basis -= non_taxable
                penalty = taxable * 0.10 if age < 59.5 else 0
                tax = taxable * 0.24
            cash_value -= withdrawal

        history.append({
            "Age": age,
            "Year": year,
            "Premium": premium,
            "COI": round(coi, 2),
            "Index Credit (%)": round(credited_return, 2),
            "Cash Value": round(cash_value, 2),
            "Cost Basis": round(cost_basis, 2),
            "Death Benefit": round(death_benefit, 2),
            "GPT Failed": gpt_failed,
            "TEFRA Failed": tefra_failed,
            "MEC Status": is_mec,
            "Withdrawal": withdrawal,
            "Policy Lapsed": cash_value == 0 and year != 1,
            "Tax on Withdrawal": round(tax, 2),
            "Penalty (if <59.5)": round(penalty, 2),
            "Death Benefit Tax-Free": not is_mec and not tefra_failed
        })

        if cash_value == 0:
            break

        age += 1

    return pd.DataFrame(history), min_required_db, death_benefit

@app.route('/')
def iul_home():
    return render_template("iul_home.html")

@app.route('/simulate', methods=['POST'])
def simulate():
    form = request.form
    df, min_required_db, actual_db = simulate_iul(
        form['test_type'],
        int(form['start_age']),
        int(form['end_age']),
        float(form['annual_premium']),
        float(form['death_benefit']),
        float(form['average_return']),
        float(form['interest_cap']),
        float(form['interest_floor']),
        float(form['initial_basis']),
        form.get('use_1035_exchange') == 'on',
        float(form['prior_cash_value']) if form.get('use_1035_exchange') == 'on' else 0,
        float(form['prior_basis']) if form.get('use_1035_exchange') == 'on' else 0,
        form.get('allow_mec') == 'on',
        int(form['withdrawal_age']) if form['withdrawal_age'] else None,
        float(form['withdrawal_amount']) if form['withdrawal_amount'] else 0,
        int(form['premium_payment_years']) if form['premium_payment_years'] else 4
    )
    df.to_excel("static/output.xlsx", index=False)

    # Check if any IRS rule failed
    if (
        df["GPT Failed"].any() or
        df["TEFRA Failed"].any() or
        df["Policy Lapsed"].any() or
        actual_db < min_required_db):
        print("Initial simulation failed IRS compliance. Running optimizer...")
        results = []

        for premium in range(50000, 150001, 25000):
            for years in range(4, 11):
                # Calculate required DB to pass GPT for earliest age (63)
                gpt_factor_est = 0.025 + 0.0003 * (int(form['start_age']) - 35)
                required_min_gpt_db = int(premium / gpt_factor_est)
                for db in range(required_min_gpt_db, required_min_gpt_db + 2000001, 250000):
                    opt_df, opt_min_db, opt_actual_db = simulate_iul(
                        form['test_type'],
                        int(form['start_age']),
                        int(form['end_age']),
                        premium, db,
                        float(form['average_return']),
                        float(form['interest_cap']),
                        float(form['interest_floor']),
                        float(form['initial_basis']),
                        form.get('use_1035_exchange') == 'on',
                        float(form['prior_cash_value']) if form.get('use_1035_exchange') == 'on' else 0,
                        float(form['prior_basis']) if form.get('use_1035_exchange') == 'on' else 0,
                        form.get('allow_mec') == 'on',
                        int(form['withdrawal_age']) if form['withdrawal_age'] else None,
                        float(form['withdrawal_amount']) if form['withdrawal_amount'] else 0,
                        years
                    )

                    gpt_fail = opt_df["GPT Failed"].any()
                    tefra_fail = opt_df["TEFRA Failed"].any()
                    lapsed = opt_df["Policy Lapsed"].any()

                    # print(f"Trying: Premium={premium}, Years={years}, DB={db} | GPT Fail={gpt_fail}, TEFRA Fail={tefra_fail}, Lapsed={lapsed}")

                    if (
                        opt_actual_db >= opt_min_db and
                        not gpt_fail and
                        not tefra_fail and
                        not lapsed
                    ):
                        final = opt_df.iloc[-1]
                        results.append({
                            "Premium": premium,
                            "Years": years,
                            "Death Benefit": db,
                            "Final Cash Value": final["Cash Value"],
                            "Final DB": final["Death Benefit"],
                            "MEC": final["MEC Status"],
                            "GPT Failed": gpt_fail,
                            "Policy Lapsed": lapsed
                        })

                        print(
                            f"✅ FOUND valid scenario: Premium={premium}, Years={years}, DB={db}, CashValue={final['Cash Value']}")

        opt_df = pd.DataFrame(results)

        if not opt_df.empty:
            opt_df.sort_values(by="Final Cash Value", ascending=False, inplace=True)
            opt_df.to_excel("static/optimization_results.xlsx", index=False)
            best_options = opt_df.head(5).to_dict(orient='records')
        else:
            best_options = []
            opt_df.to_excel("static/optimization_results.xlsx", index=False)

        print(f"👉 Returning {len(best_options)} optimized scenarios")
        return render_template("result.html",
                               table=df.to_html(classes='data', index=False),
                               chart="",
                               min_required_db=min_required_db,
                               actual_db=actual_db,
                               optimization_ran=True,
                               best_options=best_options)

    # If initial scenario passed IRS tests
    chart = pyo.plot(
        go.Figure(data=[
            go.Scatter(x=df['Age'], y=df['Cash Value'], name="Cash Value"),
            go.Scatter(x=df['Age'], y=df['Death Benefit'], name="Death Benefit")
        ]),
        output_type='div', include_plotlyjs=True
    )

    return render_template("result.html",
                           table=df.to_html(classes='data', index=False),
                           chart=chart,
                           min_required_db=min_required_db,
                           actual_db=actual_db)

@app.route('/download_excel')
def download_excel():
    return send_file('static/output.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
