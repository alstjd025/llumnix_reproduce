// ECharts 위에 얹은 그래프 함수들. 페이지는 echarts 옵션을 직접 쓰지 않고 이 함수들만 부른다.
// 시간축 그래프는 같은 group에 넣으면 확대·커서가 함께 움직인다.

const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const ENGINE_COLORS = ["#4e79a7", "#e15759", "#76b7b2", "#b07aa1", "#edc948", "#9c755f", "#ff9da7", "#bab0ac"];

function base(title, yname) {
  return {
    animation: false,
    textStyle: { color: css("--text") },
    title: { text: title, left: 4, top: 2, textStyle: { fontSize: 13, fontWeight: 600, color: css("--text") } },
    grid: { left: 56, right: 18, top: 44, bottom: 40 },
    tooltip: { trigger: "axis", confine: true, axisPointer: { label: { show: false } }, valueFormatter: v => (v == null ? "—" : (+v).toLocaleString("en-US", { maximumFractionDigits: 2 })) },
    legend: { top: 22, right: 8, textStyle: { fontSize: 11, color: css("--muted") }, itemHeight: 8 },
    yAxis: { type: "value", name: yname, nameTextStyle: { fontSize: 11, color: css("--muted") },
      axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: css("--line") } } },
  };
}

// 시간축은 type "time"으로 둔다(값은 ms). 두 축이 모두 "value"이면 ECharts가 쌓기(stack)를 가로축에 적용해
// 쌓은 그래프의 가로축이 계열 수만큼 늘어난다. time 축은 base 축으로 취급되어 세로축 값이 쌓인다.
const minLabel = v => (v / 60000).toFixed(v % 60000 ? 1 : 0);
function timeAxis(xname = "run 시작 후 (분)") {
  return { type: "time", name: xname, nameLocation: "middle", nameGap: 24, nameTextStyle: { fontSize: 11, color: css("--muted") },
    axisLabel: { color: css("--muted"), formatter: minLabel, hideOverlap: true }, min: "dataMin", max: "dataMax",
    axisPointer: { label: { formatter: p => minLabel(p.value) + "분" } }, splitLine: { show: false } };
}

function mount(el, opt, group) {
  const c = echarts.init(el, null, { renderer: "canvas" });
  c.setOption(opt);
  if (group) { c.group = group; echarts.connect(group); }
  new ResizeObserver(() => c.resize()).observe(el);
  return c;
}

const zip = (t, y) => t.map((x, i) => [x * 1000, y?.[i] ?? null]);  // 초 → ms (time 축)

// series: [{name, t, y, color?, area?, step?}], marks: [{y, label, color}] 수평선(예산 등)
export function timeLines(el, { title, yname, series, marks = [], group, stack = false, ymax }) {
  const opt = base(title, yname);
  opt.xAxis = timeAxis();
  if (ymax != null) opt.yAxis.max = ymax;
  opt.dataZoom = [{ type: "inside", xAxisIndex: 0 }];
  opt.series = series.map((s, i) => ({
    name: s.name, type: "line", showSymbol: false, data: zip(s.t, s.y), step: s.step ? "end" : false,
    lineStyle: { width: 1.3 }, color: s.color || ENGINE_COLORS[i % ENGINE_COLORS.length],
    stack: stack ? "s" : undefined, areaStyle: stack || s.area ? { opacity: 0.55 } : undefined,
    sampling: stack ? undefined : "lttb",  // 쌓은 그래프에서 솎아 내면 계열마다 다른 점이 남아 쌓기가 어긋난다
  }));
  if (marks.length && opt.series.length) {
    opt.series[0].markLine = { symbol: "none", silent: true, label: { formatter: p => p.name, fontSize: 10, color: css("--muted") },
      data: marks.map(m => ({ yAxis: m.y, name: m.label, lineStyle: { color: m.color || css("--muted"), type: "dashed" } })) };
  }
  return mount(el, opt, group);
}

// 막대(시간 bin). series: [{name, y, color, stack}]
export function timeBars(el, { title, yname, t, series, group }) {
  const opt = base(title, yname);
  opt.xAxis = timeAxis();
  opt.dataZoom = [{ type: "inside", xAxisIndex: 0 }];
  opt.series = series.map(s => ({ name: s.name, type: "bar", stack: s.stack || "s", data: zip(t, s.y), color: s.color,
    barCategoryGap: "0%", large: true }));
  return mount(el, opt, group);
}

// 누적분포. series: [{name, x(값 배열, 분위수 순서), color}], q: 분위수 배열(0~1). marks: 세로선
export function cdf(el, { title, xname, q, series, marks = [], xmax }) {
  const opt = base(title, "누적 비율");
  opt.xAxis = { type: "value", name: xname, nameLocation: "middle", nameGap: 24, max: xmax,
    nameTextStyle: { fontSize: 11, color: css("--muted") }, axisLabel: { color: css("--muted") }, splitLine: { show: false } };
  opt.yAxis.max = 1;
  opt.tooltip = { trigger: "item", confine: true };
  opt.series = series.filter(s => s.x).map(s => ({ name: s.name, type: "line", showSymbol: false, color: s.color,
    data: s.x.map((v, i) => [v, q[i]]), lineStyle: { width: 1.5 } }));
  if (marks.length && opt.series.length) {
    opt.series.forEach((s, i) => {
      const m = marks.filter(m => m.series === undefined || m.series === series[i]?.name);
      if (m.length) s.markLine = { symbol: "none", silent: true, label: { show: false },  // 예산 값은 제목에 적는다(글자가 겹친다)
        data: m.map(mm => ({ xAxis: mm.x, name: mm.label, lineStyle: { color: s.color, type: "dashed" } })) };
    });
  }
  return mount(el, opt);
}
