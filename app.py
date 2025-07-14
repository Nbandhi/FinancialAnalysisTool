from flask import Flask, render_template, request, send_file
import pandas as pd
import random
import plotly.graph_objs as go
import plotly.offline as pyo

app = Flask(__name__)

def optimize_iul_plan(total_investment, pay_years, start_age, end_age,
                      average_return, interest_cap, interest_floor,
                      withdrawal_age=None, withdrawal_amount=0,
                      use_1035_exchange=False, prior_cash_value=0, prior_basis=0,
                      allow_mec=False, test_type="GPT", optimize_goal="DB", initial_basis=0):

    best_result = None
    best_metric = -1
    annual_premium = total_investment / pay_years

    required_min_db = max(
        (annual_premium * pay_years) / 0.20,  # GPT rule of thumb
        annual_premium * 8  # MEC rule of thumb
    )

    failure_log = []

    for db_candidate in range(250000, 5000000, 25000):
        df, _, _ = simulate_iul(
            test_type=test_type,
            start_age=start_age,
            end_age=end_age,
            annual_premium=annual_premium,
            death_benefit=db_candidate,
            average_return=average_return,
            interest_cap=interest_cap,
            interest_floor=interest_floor,
            initial_basis=initial_basis,
            use_1035_exchange=use_1035_exchange,
            prior_cash_value=prior_cash_value,
            prior_basis=prior_basis,
            allow_mec=allow_mec,
            withdrawal_age=withdrawal_age,
            withdrawal_amount=withdrawal_amount,
            premium_payment_years=pay_years
        )

        # Track failure reasons
        failures = df[(df["GPT Failed"]) | (df["TEFRA Failed"]) | (df["MEC Status"])]
        if not failures.empty:
            failure_log.append((db_candidate, failures[["Age", "GPT Failed", "TEFRA Failed", "MEC Status"]]))


        all_pass = all(
            not bool(row["GPT Failed"]) and
            not bool(row["TEFRA Failed"]) and
            not bool(row["MEC Status"])
            for _, row in df.iterrows()
        )

        if all_pass:
            metric = db_candidate if optimize_goal == "DB" else df["Cash Value"].iloc[-1]
            if metric > best_metric:
                best_result = df
                best_metric = metric

    if best_result is not None:
        return {
            "Death Benefit": best_result["Death Benefit"].iloc[0],
            "Simulation": best_result
        }
    else:
        print("No valid plan passed all IRS rules. Failure log:")
        for db_val, issues in failure_log[:5]:  # Limit log size
            print(f"DB ${db_val:,.0f} failed at:")
            print(issues)
        return None

def simulate_iul(test_type, start_age, end_age, annual_premium, death_benefit,
                 average_return, interest_cap, interest_floor, initial_basis,
                 use_1035_exchange, prior_cash_value, prior_basis, allow_mec,
                 withdrawal_age=None, withdrawal_amount=0,
                 premium_payment_years=4):

    '''  Factor	Applies To	Purpose	How It's Used
        guideline_factors	GPT	Limits how much premium can be paid	premium > DB × factor
c       orridor_factors	CVAT	Ensures DB exceeds CV by a margin	DB ≥ CV × factor '''

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
            "Premium": f"${premium:,.2f}",
            "COI": f"${coi:,.2f}",
            "Index Credit (%)": round(credited_return, 2),
            "Cash Value": round(cash_value, 2),
            "Cost Basis": round(cost_basis, 2),
            "Death Benefit": round(death_benefit, 2),
            "GPT Failed": gpt_failed,
            "TEFRA Failed": tefra_failed,
            "MEC Status": is_mec,
            "Withdrawal": round(withdrawal,2),
            "Tax on Withdrawal": round(tax, 2),
            "Penalty (if <59.5)": round(penalty, 2),
            "Death Benefit Tax-Free": not is_mec and not tefra_failed
        })
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
    chart = pyo.plot(
        go.Figure(data=[
            go.Scatter(x=df['Age'], y=df['Cash Value'], name="Cash Value"),
            go.Scatter(x=df['Age'], y=df['Death Benefit'], name="Death Benefit")
        ]),
        output_type='div', include_plotlyjs=True
    )
    return render_template("result.html", table=df.to_html(classes='data', index=False),
                           chart=chart,
                           min_required_db=min_required_db,
                           actual_db=actual_db)

@app.route('/optimize', methods=['POST'])
def optimize():
    form = request.form
    result = optimize_iul_plan(
        total_investment=float(form['total_investment']),
        pay_years=int(form['premium_payment_years']),
        start_age=int(form['start_age']),
        end_age=int(form['end_age']),
        average_return=float(form['average_return']),
        interest_cap=float(form['interest_cap']),
        interest_floor=float(form['interest_floor']),
        withdrawal_age=int(form['withdrawal_age']) if form['withdrawal_age'] else None,
        withdrawal_amount=float(form['withdrawal_amount']) if form['withdrawal_amount'] else 0,
        use_1035_exchange=(form.get('use_1035_exchange') == 'on'),
        prior_cash_value=float(form['prior_cash_value']) if form.get('prior_cash_value') else 0,
        prior_basis=float(form['prior_basis']) if form.get('prior_basis') else 0,
        allow_mec=(form.get('allow_mec') == 'on'),
        test_type=form.get('test_type', 'GPT'),
        optimize_goal=form.get('optimize_goal', 'DB'),
        initial_basis=float(form['initial_basis']) if form.get('initial_basis') else 0
    )

    if not result:
        return "No valid plan found that meets all IRS rules."

    df = pd.DataFrame(result["Simulation"])
    df.to_excel("static/optimized_output.xlsx", index=False)
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
                           min_required_db=None,
                           actual_db=result["Death Benefit"])

@app.route('/download_excel')
def download_excel():
    return send_file('static/output.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)