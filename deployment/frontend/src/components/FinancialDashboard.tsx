import { useState } from "react";
import { motion } from "framer-motion";
import {
  TrendingUp,
  TrendingDown,
  Wallet,
  PiggyBank,
  CreditCard,
  ShieldCheck,
  DollarSign,
  AlertTriangle,
  Activity,
  PieChart as PieChartIcon
} from "lucide-react";
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  AreaChart, Area, PieChart, Pie, Legend
} from "recharts";
import { useUser } from "../context/UserContext";
import { fmtMoney, fmtPct, ratioHealth } from "../lib/formatters";

const container = { hidden: {}, show: { transition: { staggerChildren: 0.06 } } };
const item = { hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0 } };

const CustomTooltipStyle = {
  backgroundColor: "hsl(222,40%,10%)",
  border: "1px solid hsl(222,30%,22%)",
  borderRadius: "8px",
  fontSize: 12,
};

export const FinancialDashboard = () => {
  const { userId, userProfile, profileError, isLoadingProfile } = useUser();
  const [simulatedPurchasePrice, setSimulatedPurchasePrice] = useState(1500);

  if (!userId.trim()) {
    return (
      <div className="space-y-4 max-w-2xl">
        <h1 className="font-heading text-xl font-semibold">Financial Health Dashboard</h1>
        <p className="text-sm text-muted-foreground">Enter your **User ID** in the header to load your profile.</p>
      </div>
    );
  }

  if (isLoadingProfile) {
    return (
      <div className="space-y-4">
        <h1 className="font-heading text-xl font-semibold">Financial Health Dashboard</h1>
        <p className="text-sm text-muted-foreground">Loading your profile…</p>
      </div>
    );
  }

  if (profileError || !userProfile) {
    return (
      <div className="space-y-4 max-w-2xl">
        <h1 className="font-heading text-xl font-semibold">Financial Health Dashboard</h1>
        <div className="glass-card p-4 border-destructive/30 flex gap-3">
          <AlertTriangle className="w-5 h-5 text-destructive shrink-0" />
          <p className="text-sm text-destructive">{profileError ?? "Profile unavailable."}</p>
        </div>
      </div>
    );
  }

  const p = userProfile;
  const income = p.monthly_income ?? 0;
  const expenses = p.monthly_expenses ?? 0;
  const emi = p.monthly_emi ?? 0;
  const discretionaryRaw = p.discretionary_income;
  const discretionary =
    discretionaryRaw != null && !Number.isNaN(discretionaryRaw)
      ? Math.max(0, discretionaryRaw)
      : Math.max(0, income - expenses - emi);
  const safeDaily = Math.max(0, discretionary / 30);
  const safeWeekly = safeDaily * 7;
  const liquidSavings = p.liquid_savings ?? p.savings_balance;

  /** Monthly savings *flow* (surplus vs income), not liquid_savings ÷ income (STIR in DB). */
  const monthlyFlowSurplus =
    discretionaryRaw != null && !Number.isNaN(discretionaryRaw)
      ? discretionaryRaw
      : income - expenses - emi;
  const savingsRateMonthly =
    income > 0 && monthlyFlowSurplus != null && !Number.isNaN(monthlyFlowSurplus)
      ? monthlyFlowSurplus / income
      : null;

  const dti = p.debt_to_income_ratio;
  const dtiHealth = ratioHealth(dti, 0.28, 0.36);
  const expenseBurden = p.monthly_expense_burden_ratio;
  const ebHealth = ratioHealth(expenseBurden, 0.5, 0.65);
  const savingsRate = savingsRateMonthly;
  const srHealth =
    savingsRate == null || Number.isNaN(savingsRate)
      ? "unknown"
      : savingsRate >= 0.15
        ? "good"
        : savingsRate >= 0.08
          ? "warn"
          : "bad";
  const emer = p.emergency_fund_months;
  const emerHealth =
    emer == null || Number.isNaN(emer) ? "unknown" : emer >= 6 ? "good" : emer >= 3 ? "warn" : "bad";

  const healthColor = (h: string) =>
    h === "good" ? "text-success" : h === "warn" ? "text-caution" : h === "bad" ? "text-destructive" : "text-muted-foreground";

  const barCompare = [
    { name: "Income", value: income, fill: "hsl(160,72%,40%)" },
    { name: "Expenses", value: expenses, fill: "hsl(215,20%,55%)" },
  ];

  const stats = [
    {
      label: "Monthly Income",
      value: fmtMoney(p.monthly_income),
      change: "",
      up: true,
      icon: Wallet,
      sub: true,
    },
    {
      label: "Liquid savings",
      value: fmtMoney(liquidSavings),
      change: fmtPct(savingsRateMonthly) + " savings rate (monthly flow)",
      up: (savingsRateMonthly ?? 0) >= 0.1,
      icon: PiggyBank,
      sub: false,
    },
    {
      label: "Debt / Loan",
      value: p.has_loan ? fmtMoney(p.loan_amount) : "None",
      change: p.monthly_emi ? `${fmtMoney(p.monthly_emi)}/mo EMI` : "",
      up: false,
      icon: CreditCard,
      sub: false,
    },
    {
      label: "Credit Score",
      value: p.credit_score != null ? String(p.credit_score) : "—",
      change: "",
      up: true,
      icon: ShieldCheck,
      sub: true,
    },
  ];

  // Chart 1: Income Breakdown (Where does the money go?)
  const allocationData = [
    { name: "Essentials", value: expenses, fill: "hsl(215, 20%, 55%)" },
    { name: "Debt (EMI)", value: emi, fill: "hsl(0, 70%, 50%)" },
    { name: "Surplus / Discretionary", value: monthlyFlowSurplus, fill: "hsl(160, 72%, 40%)" }
  ].filter(d => d.value > 0);

  // Chart 2: 12-Month Trajectory Projection
  const projectionData = [];
  let currSavings = liquidSavings;
  let currDebt = p.loan_amount || 0;
  for(let m = 0; m <= 12; m++) {
    projectionData.push({
      month: m === 0 ? "Now" : `+${m}m`,
      Savings: Math.round(currSavings),
      Debt: Math.round(currDebt)
    });
    // Add monthly surplus to savings
    currSavings += Math.max(0, monthlyFlowSurplus);
    // Subtract EMI strictly from Debt
    if(currDebt > 0) currDebt = Math.max(0, currDebt - emi);
  }

  // Chart 3: Simulated Purchase Impact
  // Compare current Emergency Fund Months vs Simulated EF if they spend from Liquid Savings
  const currentEfMonths = emer ?? 0;
  const newLiquid = liquidSavings - simulatedPurchasePrice;
  const newEfMonths = expenses > 0 ? newLiquid / expenses : 0;
  const impactData = [
    { name: "Current Status", "Emergency Fund (Months)": Number(currentEfMonths.toFixed(1)), fill: "hsl(160, 72%, 40%)" },
    { name: `After $${simulatedPurchasePrice} Purchase`, "Emergency Fund (Months)": Math.max(0, Number(newEfMonths.toFixed(1))), fill: newEfMonths < 3 ? "hsl(0, 70%, 50%)" : "hsl(40, 90%, 50%)" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-xl font-semibold mb-1">Financial Health Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Snapshot from your profile — income, expenses, and key health ratios.
        </p>
      </div>

      {/* Safe to Spend Banner */}
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="glass-card p-5 border-primary/20">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-2xl bg-primary/15 flex items-center justify-center">
              <DollarSign className="w-7 h-7 text-primary" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">Est. safe to spend / day</p>
              <p className="font-heading text-3xl font-bold text-primary">${Math.round(safeDaily)}</p>
              <p className="text-[11px] text-muted-foreground mt-1">
                From monthly discretionary ({fmtMoney(discretionary)}) ÷ 30
              </p>
            </div>
          </div>
          <div className="flex gap-6 flex-wrap">
            <div className="text-center">
              <p className="text-xs text-muted-foreground">This Week (est.)</p>
              <p className="font-heading text-lg font-semibold">${Math.round(safeWeekly)}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-muted-foreground">Discretionary (mo)</p>
              <p className="font-heading text-lg font-semibold">{fmtMoney(discretionary)}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-muted-foreground">Monthly income</p>
              <p className="font-heading text-lg font-semibold">{fmtMoney(income)}</p>
            </div>
          </div>
        </div>
      </motion.div>

      {/* Stats */}
      <motion.div variants={container} initial="hidden" animate="show" className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <motion.div key={s.label} variants={item} className="glass-card p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground font-medium">{s.label}</span>
              <s.icon className="w-4 h-4 text-muted-foreground" />
            </div>
            <p className="font-heading text-2xl font-semibold">{s.value}</p>
            {s.change ? (
              <span
                className={`inline-flex items-center gap-1 text-xs font-medium ${
                  s.sub ? "text-muted-foreground" : s.up ? "text-success" : "text-destructive"
                }`}
              >
                {!s.sub && (s.up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />)}
                {s.change}
              </span>
            ) : null}
          </motion.div>
        ))}
      </motion.div>

      {/* Health gauges */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="glass-card p-5">
        <h2 className="font-heading text-sm font-semibold mb-4">Financial health indicators</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Debt-to-income</p>
            <p className={`text-lg font-semibold ${healthColor(dtiHealth)}`}>
              {dti != null ? fmtPct(dti) : "—"}
            </p>
            <p className="text-[11px] text-muted-foreground">Lower is better (typ. &lt;28% good)</p>
          </div>
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Emergency fund</p>
            <p className={`text-lg font-semibold ${healthColor(emerHealth)}`}>
              {emer != null ? `${emer.toFixed(1)} mo` : "—"}
            </p>
            <p className="text-[11px] text-muted-foreground">Target often 3–6+ months</p>
          </div>
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Savings rate</p>
            <p className={`text-lg font-semibold ${healthColor(srHealth)}`}>{fmtPct(savingsRate)}</p>
            <p className="text-[11px] text-muted-foreground">Monthly surplus ÷ income (not liquid balance)</p>
          </div>
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Expense burden</p>
            <p className={`text-lg font-semibold ${healthColor(ebHealth)}`}>
              {expenseBurden != null ? fmtPct(expenseBurden) : "—"}
            </p>
            <p className="text-[11px] text-muted-foreground">Expenses ÷ income</p>
          </div>
        </div>
      </motion.div>

      {/* Dashboard Custom AI Charts */}
      <div className="grid lg:grid-cols-2 gap-6">
        
        {/* Chart 1: Income Allocation Donut */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }} className="glass-card p-5">
          <div className="flex items-center gap-2 mb-1">
            <PieChartIcon className="w-4 h-4 text-primary" />
            <h2 className="font-heading text-sm font-semibold">Monthly Income Allocation</h2>
          </div>
          <p className="text-xs text-muted-foreground mb-4">Where your income is distributed</p>
          <div className="h-64">
            <ResponsiveContainer>
              <PieChart>
                <Tooltip contentStyle={CustomTooltipStyle} formatter={(v: number) => `$${v.toLocaleString()}`} />
                <Legend 
                  verticalAlign="bottom" 
                  height={36} 
                  iconSize={14}
                  wrapperStyle={{ 
                    fontSize: '13px', 
                    display: 'flex', 
                    justifyContent: 'center',
                    paddingTop: '10px'
                  }}
                  formatter={(value) => <span style={{ marginLeft: '4px', marginRight: '28px', color: 'hsl(215, 20%, 65%)' }}>{value}</span>}
                />
                <Pie 
                  data={allocationData} 
                  dataKey="value" 
                  nameKey="name" 
                  cx="50%" 
                  cy="45%" 
                  innerRadius={50} 
                  outerRadius={75} 
                  paddingAngle={5} 
                  stroke="none"
                  label={({ x, y, value, cx }) => (
                    <text x={x} y={y} fill="hsl(215, 20%, 65%)" textAnchor={x > cx ? 'start' : 'end'} dominantBaseline="central" fontSize={12} fontWeight={500}>
                      ${Math.round(value).toLocaleString()}
                    </text>
                  )}
                  labelLine={{ stroke: 'hsl(215, 20%, 40%)', strokeWidth: 1 }}
                >
                  {allocationData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.fill} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
          </div>
        </motion.div>

        {/* Chart 2: 12-Month Projection */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }} className="glass-card p-5">
          <div className="flex items-center gap-2 mb-1">
            <Activity className="w-4 h-4 text-primary" />
            <h2 className="font-heading text-sm font-semibold">12-Month Wealth Trajectory</h2>
          </div>
          <p className="text-xs text-muted-foreground mb-4">Savings growth vs Debt paydown (assuming flat cash flow)</p>
          <div className="h-64">
            <ResponsiveContainer>
              <AreaChart data={projectionData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorSavings" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="hsl(160,72%,40%)" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="hsl(160,72%,40%)" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorDebt" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="hsl(0,70%,50%)" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="hsl(0,70%,50%)" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="month" stroke="hsl(215,20%,55%)" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis stroke="hsl(215,20%,55%)" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `$${v / 1000}k`} />
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(222,30%,18%)" />
                <Tooltip contentStyle={CustomTooltipStyle} formatter={(v: number) => `$${v.toLocaleString()}`} />
                <Area type="monotone" dataKey="Savings" stroke="hsl(160,72%,40%)" fillOpacity={1} fill="url(#colorSavings)" strokeWidth={2} />
                <Area type="monotone" dataKey="Debt" stroke="hsl(0,70%,50%)" fillOpacity={1} fill="url(#colorDebt)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </motion.div>

        {/* Chart 3: Interactive Purchase Impact Simulator */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }} className="glass-card p-5 lg:col-span-2">
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className="w-4 h-4 text-primary" />
            <h2 className="font-heading text-sm font-semibold">Interactive Purchase Impact Simulator</h2>
          </div>
          <p className="text-xs text-muted-foreground mb-4">See how a major hypothetical purchase impacts your core Emergency Fund ratio</p>
          
          <div className="flex flex-col md:flex-row gap-6 items-center">
            <div className="w-full md:w-1/3 bg-secondary/30 p-4 rounded-xl space-y-4">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Hypothetical Purchase Amount</label>
                <div className="flex items-center gap-2">
                  <span className="text-lg font-bold text-primary">$</span>
                  <input 
                    type="range" 
                    min="100" 
                    max="10000" 
                    step="100" 
                    value={simulatedPurchasePrice} 
                    onChange={(e) => setSimulatedPurchasePrice(Number(e.target.value))}
                    className="flex-1 accent-primary"
                  />
                </div>
                <p className="text-xl font-heading font-semibold text-center mt-2">${simulatedPurchasePrice.toLocaleString()}</p>
              </div>
              <div className="pt-2 border-t border-border/50">
                <p className="text-xs text-muted-foreground mb-1">Remaining Liquid Savings</p>
                <p className={`font-semibold ${newLiquid < 0 ? 'text-destructive' : 'text-foreground'}`}>
                  ${newLiquid.toLocaleString()}
                </p>
              </div>
            </div>

            <div className="h-52 w-full md:w-2/3">
              <ResponsiveContainer>
                <BarChart data={impactData} layout="vertical" margin={{ top: 0, right: 30, left: 30, bottom: 0 }} barSize={35}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="hsl(222,30%,18%)" />
                  <XAxis type="number" stroke="hsl(215,20%,55%)" fontSize={11} domain={[0, 'dataMax + 2']} hide/>
                  <YAxis type="category" dataKey="name" stroke="hsl(215,20%,55%)" fontSize={11} width={120} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={CustomTooltipStyle} cursor={{fill: 'transparent'}} />
                  <Bar dataKey="Emergency Fund (Months)" radius={[0, 4, 4, 0]}>
                    {impactData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.fill} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </motion.div>
      </div>

      {/* Loan summary */}
      {p.has_loan ? (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-5 border-border/60"
        >
          <h2 className="font-heading text-sm font-semibold mb-3">Loan summary</h2>
          <div className="grid sm:grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-[10px] uppercase text-muted-foreground">Outstanding</p>
              <p className="font-semibold">{fmtMoney(p.loan_amount)}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-muted-foreground">Monthly EMI</p>
              <p className="font-semibold">{fmtMoney(p.monthly_emi)}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-muted-foreground">Interest rate</p>
              <p className="font-semibold">{p.loan_interest_rate != null ? `${p.loan_interest_rate}%` : "—"}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-muted-foreground">Term (months)</p>
              <p className="font-semibold">{p.loan_term_months != null ? Math.round(p.loan_term_months) : "—"}</p>
            </div>
          </div>
        </motion.div>
      ) : null}
    </div>
  );
};
