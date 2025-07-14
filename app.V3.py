# Updated app.py with 2-phase premium logic
# UPDATED app.py with 2-phase premium logic (GSP -> GLP strategy)

from flask import Flask, render_template, request, send_file
import pandas as pd
import random
import plotly.graph_objs as go
import plotly.offline as pyo

app = Flask(__name__)

def simulate_iul(test_type, start_age, end_age, initial_premium_amount, ongoing_premium_amount,
                 death_benefit, average_return, interest_cap, interest_floor, initial_basis,
                 use_1035_exchange, prior_cash_value, prior_basis, allow_mec,
                 initial_premium_years=3, total_premium_years=7,
                 withdrawal_age=None, withdrawal_amount=0):

    guideline_factors = {age: 0.025 + 0.0003 * (age - 35) for age in range(35, 86)}
    corridor_factors = {age: max(1.0 + 0.5 - (age - 35) * 0.01, 1.0) for age in range(35, 86)}

    # Estimate IRS-required death benefit for GPT and MEC
    total_funding = initial_premium_amount * initial_premium_years + ongoing_premium_amount * (total_premium_years - initial_premium_years)
    min_required_db = max(total_funding / 0.20, initial_premium_amount * 8)

    cash_value = prior_cash_value if use_1035_exchange else 0
    cost_basis = prior_basis if use_1035_exchange else initial_basis
    age = start_age
    history = []

    for year in range(1, end_age - start_age + 2):
        if year <= initial_premium_years:
            premium = initial_premium_amount
        elif year <= total_premium_years:
            premium = ongoing_premium_amount
        else:
            premium = 0

        gpt_failed = premium > death_benefit * guideline_factors.get(age, 0.03)

        cash_value += premium
        cost_basis += premium

        coi = death_benefit * (0.005 + 0.0005 * (age - start_age))
        if cash_value < coi:
            history.append({"Age": age, "Year": year, "Premium": premium, "COI": coi, "Cash Value": 0,
                            "Death Benefit": 0, "GPT Failed": gpt_failed, "TEFRA Failed": True,
                            "MEC Status": True, "Withdrawal": 0, "Policy Lapsed": True,
                            "Tax on Withdrawal": 0, "Penalty (if <59.5)": 0, "Death Benefit Tax-Free": False})
            break

        cash_value -= coi
        credited_return = min(max(random.gauss(average_return, 0.01), interest_floor), interest_cap)
        cash_value += cash_value * (credited_return / 100)

        tefra_failed = cash_value > (death_benefit * corridor_factors.get(age, 1.0))
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

        history.append({"Age": age, "Year": year, "Premium": premium, "COI": round(coi, 2),
                        "Cash Value": round(cash_value, 2), "Cost Basis": round(cost_basis, 2),
                        "Death Benefit": round(death_benefit, 2), "GPT Failed": gpt_failed,
                        "TEFRA Failed": tefra_failed, "MEC Status": is_mec, "Withdrawal": withdrawal,
                        "Policy Lapsed": cash_value == 0 and year != 1, "Tax on Withdrawal": round(tax, 2),
                        "Penalty (if <59.5)": round(penalty, 2), "Death Benefit Tax-Free": not is_mec and not tefra_failed})

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
        form['test_type'], int(form['start_age']), int(form['end_age']),
        float(form['initial_premium_amount']), float(form['ongoing_premium_amount']),
        float(form['death_benefit']), float(form['average_return']),
        float(form['interest_cap']), float(form['interest_floor']),
        float(form['initial_basis']),
        form.get('use_1035_exchange') == 'on',
        float(form['prior_cash_value']) if form.get('use_1035_exchange') == 'on' else 0,
        float(form['prior_basis']) if form.get('use_1035_exchange') == 'on' else 0,
        form.get('allow_mec') == 'on',
        int(form['initial_premium_years']), int(form['total_premium_years']),
        int(form['withdrawal_age']) if form['withdrawal_age'] else None,
        float(form['withdrawal_amount']) if form['withdrawal_amount'] else 0
    )

    df.to_excel("static/output.xlsx", index=False)

    chart = pyo.plot(
        go.Figure(data=[
            go.Scatter(x=df['Age'], y=df['Cash Value'], name="Cash Value"),
            go.Scatter(x=df['Age'], y=df['Death Benefit'], name="Death Benefit")
        ]),
        output_type='div', include_plotlyjs=True
    )

    return render_template("result.html", table=df.to_html(classes='data', index=False),
                           chart=chart, min_required_db=min_required_db,
                           actual_db=actual_db)

@app.route('/download_excel')
def download_excel():
    return send_file('static/output.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
