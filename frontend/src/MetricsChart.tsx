import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export default function MetricsChart({
  data,
  max,
}: {
  data: {
    name: string;
    effects: number;
    duplicates: number;
    confirmed: number;
  }[];
  max: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={265}>
      <BarChart data={data} barGap={6}>
        <CartesianGrid stroke="#e8edf4" vertical={false} />
        <XAxis
          dataKey="name"
          tickLine={false}
          axisLine={false}
          tick={{ fill: "#66758d", fontSize: 12 }}
        />
        <YAxis
          allowDecimals={false}
          tickLine={false}
          axisLine={false}
          tick={{ fill: "#66758d", fontSize: 12 }}
          domain={[0, Math.max(2, max + 1)]}
        />
        <Tooltip
          contentStyle={{ border: "1px solid #e3eaf3", borderRadius: 14 }}
        />
        <Legend />
        <Bar
          dataKey="effects"
          fill="#c9d7f8"
          name="Effects"
          radius={[5, 5, 0, 0]}
        />
        <Bar
          dataKey="confirmed"
          fill="#3866e9"
          name="Verified confirmations"
          radius={[5, 5, 0, 0]}
        />
        <Bar
          dataKey="duplicates"
          fill="#e89475"
          name="Duplicates"
          radius={[5, 5, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
