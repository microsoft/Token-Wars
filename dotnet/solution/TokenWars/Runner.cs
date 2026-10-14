using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TokenWars;

/// <summary>One line of shared/data/workload.jsonl (ground-truth fields are for the judge only).</summary>
public sealed class WorkItem
{
    public string Id { get; init; } = "";
    public string CustomerId { get; init; } = "";
    public string Question { get; init; } = "";
    public string ReferenceAnswer { get; init; } = "";
    public List<string> MustInclude { get; init; } = new();
    public bool Compare { get; init; }

    /// <summary>Only id / customer_id / question go into the pipeline.</summary>
    public QuestionInput ToInput() => new(Id, CustomerId, Question);
}

/// <summary>Run summary (SPEC 4.8).</summary>
public sealed class Summary
{
    public int Items { get; init; }
    public int Successes { get; init; }
    public double PassRate { get; init; }
    public bool Valid { get; init; }
    public double TotalCostUsd { get; init; }
    public double CostPerSuccessUsd { get; init; }
    public long InputTokens { get; init; }
    public long CachedInputTokens { get; init; }
    public long OutputTokens { get; init; }
    public int CacheHitsExact { get; init; }
    public int CacheHitsSemantic { get; init; }
    public int Escalations { get; init; }
    public long LatencyP50Ms { get; init; }
    public long LatencyP95Ms { get; init; }
    public double JudgeCostUsd { get; init; }
    public SortedDictionary<string, ModelUsage> CallsByModel { get; init; } = new(StringComparer.Ordinal);

    // compare only
    public double? AvgScore { get; set; }
    public int? Errors { get; set; }

    public JsonObject ToJson()
    {
        var byModel = new JsonObject();
        foreach (var (key, usage) in CallsByModel) byModel[key] = usage.ToJson();
        var json = new JsonObject
        {
            ["items"] = Items,
            ["successes"] = Successes,
            ["pass_rate"] = PassRate,
            ["valid"] = Valid,
            ["total_cost_usd"] = TotalCostUsd,
            ["cost_per_success_usd"] = CostPerSuccessUsd,
            ["input_tokens"] = InputTokens,
            ["cached_input_tokens"] = CachedInputTokens,
            ["output_tokens"] = OutputTokens,
            ["cache_hits_exact"] = CacheHitsExact,
            ["cache_hits_semantic"] = CacheHitsSemantic,
            ["escalations"] = Escalations,
            ["latency_p50_ms"] = LatencyP50Ms,
            ["latency_p95_ms"] = LatencyP95Ms,
            ["judge_cost_usd"] = JudgeCostUsd,
            ["calls_by_model"] = byModel,
        };
        if (Errors.HasValue)
        {
            json["avg_score"] = AvgScore;
            json["errors"] = Errors.Value;
        }
        return json;
    }
}

/// <summary>Commands: run, compare, ask, doctor (+ leaderboard submit).</summary>
public static class Runner
{
    private static readonly QuestionInput PreflightItem = new("preflight", "C1001", "Can I return shoes I bought last week?");
    private static readonly object PrintLock = new();
    private static readonly HttpClient SubmitHttp = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromSeconds(30)
    };

    private static void Say(string text = "")
    {
        lock (PrintLock)
        {
            Console.WriteLine(text);
        }
    }

    // ------------------------------------------------------------------ helpers

    public static List<WorkItem> LoadWorkload(string root)
    {
        var path = Path.Combine(root, "shared", "data", "workload.jsonl");
        if (!File.Exists(path)) throw new ConfigException($"{path} not found");
        var items = new List<WorkItem>();
        int lineNo = 0;
        foreach (var line in File.ReadAllLines(path))
        {
            lineNo++;
            if (string.IsNullOrWhiteSpace(line)) continue;
            JsonObject? o;
            try
            {
                o = JsonNode.Parse(line) as JsonObject;
            }
            catch (JsonException ex)
            {
                throw new ConfigException($"{path} line {lineNo} is not valid JSON: {ex.Message}");
            }
            if (o == null) throw new ConfigException($"{path} line {lineNo} is not a JSON object");

            var must = new List<string>();
            if (o["must_include"] is JsonArray arr)
            {
                foreach (var m in arr)
                {
                    var s = JsonUtil.Str(m);
                    if (s != null) must.Add(s);
                }
            }
            items.Add(new WorkItem
            {
                Id = JsonUtil.Str(o["id"]) ?? $"line{lineNo}",
                CustomerId = JsonUtil.Str(o["customer_id"]) ?? "",
                Question = JsonUtil.Str(o["question"]) ?? "",
                ReferenceAnswer = JsonUtil.Str(o["reference_answer"]) ?? "",
                MustInclude = must,
                Compare = JsonUtil.Bool(o["compare"]) ?? false,
            });
        }
        return items;
    }

    /// <summary>Runs one fake item fully offline so an enabled-but-unimplemented TODO fails fast, before any spend.</summary>
    public static async Task PreflightAsync(AppConfig cfg, Strategy strategy)
    {
        var client = new LlmClient(cfg, useGateway: false, retryOnThrottle: strategy.RetryOnThrottle, mock: true);
        await new Pipeline(cfg, strategy, client, persistEmbeddings: false).AnswerAsync(PreflightItem);
    }

    public static (string Timestamp, string Stamp) UtcStamp()
    {
        var now = DateTime.UtcNow;
        return (now.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
                now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture));
    }

    /// <summary>Nearest-rank percentile.</summary>
    public static long Percentile(List<long> values, double pct)
    {
        if (values.Count == 0) return 0;
        var ordered = values.OrderBy(v => v).ToList();
        int rank = Math.Max(1, (int)Math.Ceiling(pct / 100.0 * ordered.Count));
        return ordered[rank - 1];
    }

    public static string FmtUsd(double value) =>
        "$" + value.ToString(value < 0.01 ? "N6" : "N4", CultureInfo.InvariantCulture);

    private static string N0(long value) => value.ToString("N0", CultureInfo.InvariantCulture);

    private static string Pct(double rate, int decimals) =>
        (rate * 100).ToString(decimals == 0 ? "0" : "0." + new string('0', decimals), CultureInfo.InvariantCulture) + "%";

    private static void MockBanner(AppConfig cfg)
    {
        if (cfg.Mock)
        {
            Say("🧪 MOCK MODE – no Azure calls; answers are fake and costs are simulated from character counts.");
        }
        foreach (var warning in cfg.Warnings) Say($"⚠️  {warning}");
    }

    /// <summary>Answers items with `concurrency` workers; results stay in file order. Fails fast on NotImplementedException.</summary>
    public static async Task<List<ItemResult>> AnswerAllAsync(
        Pipeline pipeline, IReadOnlyList<WorkItem> items, int concurrency, bool progress = true)
    {
        int total = items.Count;
        int done = 0;
        var results = new ItemResult?[total];
        using var gate = new SemaphoreSlim(Math.Max(1, concurrency));
        using var cts = new CancellationTokenSource();
        Exception? fatal = null;

        async Task Work(int index)
        {
            try
            {
                await gate.WaitAsync(cts.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            try
            {
                var result = await pipeline.AnswerAsync(items[index].ToInput(), cts.Token);
                results[index] = result;
                if (progress)
                {
                    lock (PrintLock)
                    {
                        done++;
                        var path = result.ModelPath.Count > 0 ? string.Join("→", result.ModelPath) : "-";
                        var line =
                            $"  [{done,3}/{total}] {result.Id,-6} {path,-22} cache:{result.Cache,-8} " +
                            $"{result.InputTokens,7} in {result.OutputTokens,5} out  {FmtUsd(result.CostUsd)}";
                        if (result.Error != null) line += $"  ❌ {Truncate(result.Error, 100)}";
                        Console.WriteLine(line);
                    }
                }
            }
            catch (OperationCanceledException) when (cts.IsCancellationRequested)
            {
                // another item failed fatally
            }
            catch (Exception ex)
            {
                Interlocked.CompareExchange(ref fatal, ex, null);
                cts.Cancel();
            }
            finally
            {
                gate.Release();
            }
        }

        var tasks = new List<Task>();
        for (int i = 0; i < total; i++) tasks.Add(Work(i));
        await Task.WhenAll(tasks);

        if (fatal != null)
        {
            System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(fatal).Throw();
        }
        return results.Select(r => r!).ToList();
    }

    /// <summary>Fills judge_score / success on every item. Returns the judge cost (not part of the team score).</summary>
    public static async Task<double> JudgeAllAsync(
        AppConfig cfg, List<ItemResult> results, Dictionary<string, WorkItem> workload, bool enabled)
    {
        int passScore = cfg.Scoring.PassScore;
        var toJudge = new List<ItemResult>();
        foreach (var result in results)
        {
            if (result.Error != null)
            {
                result.JudgeScore = 0;
                result.JudgeReason = "error";
                result.Success = false;
            }
            else if (!enabled)
            {
                result.JudgeScore = null;
                result.JudgeReason = "not judged";
                result.Success = false;
            }
            else
            {
                toJudge.Add(result);
            }
        }
        if (toJudge.Count == 0) return 0.0;

        int concurrency = cfg.Scoring.JudgeConcurrency;
        Say($"\n⚖️  Judging {toJudge.Count} answers (concurrency {concurrency}) ...");
        var judge = new Judge(cfg, new LlmClient(cfg, useGateway: false, retryOnThrottle: false));
        using var gate = new SemaphoreSlim(Math.Max(1, concurrency));

        async Task<double> Work(ItemResult result)
        {
            await gate.WaitAsync();
            try
            {
                var truth = workload[result.Id];
                var verdict = await judge.JudgeAsync(truth.Question, truth.ReferenceAnswer, truth.MustInclude, result.Answer, truth.CustomerId);
                result.JudgeScore = verdict.Score;
                result.JudgeReason = verdict.Reason;
                result.Success = verdict.Score >= passScore;
                return verdict.CostUsd;
            }
            finally
            {
                gate.Release();
            }
        }

        var costs = await Task.WhenAll(toJudge.Select(r => Work(r)));
        return costs.Sum();
    }

    public static Summary Summarize(AppConfig cfg, List<ItemResult> results, double judgeCost)
    {
        int items = results.Count;
        int successes = results.Count(r => r.Success);
        double passRate = items > 0 ? (double)successes / items : 0.0;
        double totalCost = results.Sum(r => r.CostUsd);
        var latencies = results.Select(r => r.LatencyMs).ToList();
        var byModel = ModelUsage.ByModel(results.SelectMany(r => r.Calls));
        return new Summary
        {
            Items = items,
            Successes = successes,
            PassRate = Math.Round(passRate, 4),
            Valid = passRate >= cfg.Scoring.MinPassRate,
            TotalCostUsd = JsonUtil.Money(totalCost),
            CostPerSuccessUsd = JsonUtil.Money(totalCost / Math.Max(successes, 1)),
            InputTokens = results.Sum(r => (long)r.InputTokens),
            CachedInputTokens = results.Sum(r => (long)r.CachedInputTokens),
            OutputTokens = results.Sum(r => (long)r.OutputTokens),
            CacheHitsExact = results.Count(r => r.Cache == "exact"),
            CacheHitsSemantic = results.Count(r => r.Cache == "semantic"),
            Escalations = results.Sum(r => r.Escalations),
            LatencyP50Ms = Percentile(latencies, 50),
            LatencyP95Ms = Percentile(latencies, 95),
            JudgeCostUsd = JsonUtil.Money(judgeCost),
            CallsByModel = byModel,
        };
    }

    private static int DisplayWidth(string text) => text.Length + text.Count(ch => ch == '✅' || ch == '❌');

    public static void PrintScorecard(AppConfig cfg, Summary summary, bool judged)
    {
        const int width = 64;
        double minRate = cfg.Scoring.MinPassRate;
        string verdict;
        if (!judged) verdict = "NOT JUDGED (--no-judge) – pass rate unknown";
        else if (summary.Valid) verdict = "VALID ✅";
        else verdict = $"INVALID ❌ (pass rate below {Pct(minRate, 0)})";

        var rows = new List<string>
        {
            $"TOKEN WARS SCORECARD – {cfg.Team} ({cfg.Language}/{cfg.Variant}){(cfg.Mock ? " [MOCK]" : "")}",
            "",
            $"Items / successes      {summary.Items} / {summary.Successes}",
            judged ? $"Pass rate              {Pct(summary.PassRate, 1)}" : "Pass rate              n/a (not judged)",
            $"Total cost             {FmtUsd(summary.TotalCostUsd)}",
            $"Cost per success       {FmtUsd(summary.CostPerSuccessUsd)}",
            $"Input tokens           {N0(summary.InputTokens)} ({N0(summary.CachedInputTokens)} cached)",
            $"Output tokens          {N0(summary.OutputTokens)}",
            $"Cache hits             {summary.CacheHitsExact} exact / {summary.CacheHitsSemantic} semantic",
            $"Escalations            {summary.Escalations}",
            $"Latency p50 / p95      {summary.LatencyP50Ms} ms / {summary.LatencyP95Ms} ms",
            $"Judge cost (unscored) {FmtUsd(summary.JudgeCostUsd)}",
            "",
            verdict,
        };

        var border = "+" + new string('-', width + 2) + "+";
        var lines = new List<string> { border };
        foreach (var row in rows)
        {
            lines.Add("| " + row + new string(' ', Math.Max(width - DisplayWidth(row), 0)) + " |");
        }
        lines.Add(border);
        var header = "model".PadRight(12) + "calls".PadLeft(7) + "input".PadLeft(11) + "cached".PadLeft(9)
                     + "output".PadLeft(9) + "cost".PadLeft(14);
        lines.Add("| " + header.PadRight(width) + " |");
        foreach (var (key, entry) in summary.CallsByModel)
        {
            var row = key.PadRight(12) + entry.Calls.ToString(CultureInfo.InvariantCulture).PadLeft(7)
                      + N0(entry.InputTokens).PadLeft(11) + N0(entry.CachedInputTokens).PadLeft(9)
                      + N0(entry.OutputTokens).PadLeft(9) + FmtUsd(JsonUtil.Money(entry.CostUsd)).PadLeft(14);
            lines.Add("| " + row.PadRight(width) + " |");
        }
        lines.Add(border);
        Say("\n" + string.Join("\n", lines));
    }

    public static string WriteJson(AppConfig cfg, string prefix, JsonObject payload, string stamp)
    {
        Directory.CreateDirectory(cfg.ResultsDir);
        var path = Path.Combine(cfg.ResultsDir, $"{prefix}-{stamp}.json");
        File.WriteAllText(path, payload.ToJsonString(JsonUtil.Indented), new UTF8Encoding(false));
        return path;
    }

    public static async Task SubmitAsync(AppConfig cfg, JsonObject body)
    {
        var url = cfg.GetEnv("TOKENWARS_LEADERBOARD_URL").Trim().TrimEnd('/');
        if (url.Length == 0)
        {
            Say("⚠️  TOKENWARS_LEADERBOARD_URL is not set – skipping submit.");
            return;
        }
        try
        {
            var key = cfg.GetEnv("TOKENWARS_LEADERBOARD_KEY").Trim();
            var endpoint = new Uri(url + "/api/submissions");
            var json = body.ToJsonString(JsonUtil.Compact);
            using var response = await SendSubmissionAsync(endpoint, json, key);
            if (IsRedirect(response) && response.Headers.Location is Uri location)
            {
                var redirected = location.IsAbsoluteUri ? location : new Uri(endpoint, location);
                var sameOrigin = endpoint.Scheme.Equals(redirected.Scheme, StringComparison.OrdinalIgnoreCase)
                    && endpoint.Authority.Equals(redirected.Authority, StringComparison.OrdinalIgnoreCase);
                var httpsUpgrade = endpoint.Scheme == Uri.UriSchemeHttp
                    && redirected.Scheme == Uri.UriSchemeHttps
                    && endpoint.Host.Equals(redirected.Host, StringComparison.OrdinalIgnoreCase)
                    && endpoint.IsDefaultPort && redirected.IsDefaultPort;
                var safeRedirect = sameOrigin || httpsUpgrade;
                if (!safeRedirect)
                {
                    Say($"❌ Leaderboard refused unsafe redirect to {redirected}");
                    return;
                }
                using var redirectedResponse = await SendSubmissionAsync(redirected, json, key);
                await ReportSubmissionResponseAsync(redirectedResponse);
                return;
            }
            await ReportSubmissionResponseAsync(response);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or InvalidOperationException or UriFormatException)
        {
            Say($"❌ Submit failed: {ex.Message}");
        }
    }

    private static async Task<HttpResponseMessage> SendSubmissionAsync(Uri endpoint, string json, string key)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, endpoint);
        request.Content = new StringContent(json, Encoding.UTF8, "application/json");
        if (key.Length > 0) request.Headers.TryAddWithoutValidation("x-submit-key", key);
        return await SubmitHttp.SendAsync(request);
    }

    private static bool IsRedirect(HttpResponseMessage response) =>
        (int)response.StatusCode is 301 or 302 or 307 or 308;

    private static async Task ReportSubmissionResponseAsync(HttpResponseMessage response)
    {
        var text = await response.Content.ReadAsStringAsync();
        JsonNode? result = null;
        try
        {
            result = JsonNode.Parse(text);
        }
        catch (JsonException)
        {
            // Report the unexpected response below.
        }
        if ((int)response.StatusCode != 201 || result?["accepted"]?.GetValue<bool>() != true)
        {
            Say($"❌ Leaderboard rejected the submission (HTTP {(int)response.StatusCode}): {Truncate(text, 500)}");
            return;
        }
        Say($"📤 Leaderboard accepted the submission (HTTP 201): {Truncate(text, 500)}");
    }

    private static string Truncate(string text, int max) => text.Length > max ? text.Substring(0, max) : text;

    private static JsonObject Envelope(AppConfig cfg, Strategy strategy, string timestamp) => new()
    {
        ["schema_version"] = 1,
        ["team"] = cfg.Team,
        ["language"] = cfg.Language,
        ["variant"] = cfg.Variant,
        ["timestamp"] = timestamp,
        ["mock"] = cfg.Mock,
        ["strategy"] = strategy.ToJson(),
    };

    // ------------------------------------------------------------------ commands

    public static async Task<int> RunAsync(AppConfig cfg, Strategy strategy, int? limit, bool judgeEnabled, bool doSubmit)
    {
        MockBanner(cfg);
        await PreflightAsync(cfg, strategy);
        var workload = LoadWorkload(cfg.Root);
        if (limit.HasValue) workload = workload.Take(Math.Max(limit.Value, 0)).ToList();
        Say($"🏁 Running {workload.Count} items with strategy {Path.GetFileName(cfg.StrategyPath)} " +
            $"(default_model={strategy.DefaultModel}, routing={strategy.Routing}, retrieval={strategy.Retrieval}, " +
            $"concurrency={strategy.Concurrency})");

        var client = new LlmClient(cfg, strategy.UseGateway, strategy.RetryOnThrottle);
        var pipeline = new Pipeline(cfg, strategy, client);
        var results = await AnswerAllAsync(pipeline, workload, strategy.Concurrency);
        var byId = BuildIndex(workload);
        var judgeCost = await JudgeAllAsync(cfg, results, byId, judgeEnabled);
        var summary = Summarize(cfg, results, judgeCost);
        PrintScorecard(cfg, summary, judgeEnabled);

        var (timestamp, stamp) = UtcStamp();
        var payload = Envelope(cfg, strategy, timestamp);
        payload["summary"] = summary.ToJson();
        var itemsJson = new JsonArray();
        foreach (var r in results) itemsJson.Add((JsonNode)r.ToJson());
        payload["items"] = itemsJson;
        var path = WriteJson(cfg, "run", payload, stamp);
        Say($"💾 Results written to {path}");

        if (doSubmit)
        {
            // fresh nodes (a JsonNode can only have one parent); the leaderboard body has no schema_version (SPEC 4.8)
            var body = Envelope(cfg, strategy, timestamp);
            body.Remove("schema_version");
            body["summary"] = summary.ToJson();
            await SubmitAsync(cfg, body);
        }
        return 0;
    }

    public static async Task<int> CompareAsync(AppConfig cfg, Strategy strategy, List<string> models, bool judgeEnabled, int? limit)
    {
        MockBanner(cfg);
        var workload = LoadWorkload(cfg.Root).Where(w => w.Compare).ToList();
        if (limit.HasValue) workload = workload.Take(Math.Max(limit.Value, 0)).ToList();
        var byId = BuildIndex(workload);
        Say($"🔬 Comparing {workload.Count} compare:true items across models: {string.Join(", ", models)} " +
            "(current prompt strategy; no routing, no caching, no escalation)");

        var rows = new List<(string Model, string Deployment, double HourlyCostUsd, Summary Summary, List<ItemResult> Items)>();
        foreach (var key in models)
        {
            if (!cfg.Models.TryGetValue(key, out var model) || model.Type != "chat")
            {
                Say($"⚠️  Skipping '{key}': not a configured chat model in {cfg.ModelsSource}");
                continue;
            }
            var modelStrategy = strategy.Clone();
            modelStrategy.Routing = "none";
            modelStrategy.DefaultModel = key;
            modelStrategy.ExactCache = false;
            modelStrategy.SemanticCache = false;
            modelStrategy.Escalation = false;
            await PreflightAsync(cfg, modelStrategy);
            Say($"\n▶ {key} ({model.Deployment})");

            var client = new LlmClient(cfg, strategy.UseGateway, strategy.RetryOnThrottle);
            var results = await AnswerAllAsync(new Pipeline(cfg, modelStrategy, client), workload, strategy.Concurrency, progress: false);
            var judgeCost = await JudgeAllAsync(cfg, results, byId, judgeEnabled);
            var summary = Summarize(cfg, results, judgeCost);
            var scores = results.Where(r => r.JudgeScore.HasValue).Select(r => r.JudgeScore!.Value).ToList();
            summary.AvgScore = scores.Count > 0 ? (double?)Math.Round(scores.Average(), 3) : null;
            summary.Errors = results.Count(r => r.Error != null);
            rows.Add((key, model.Deployment, model.HourlyCostUsd, summary, results));
        }

        // Optional fixed hosting fee (models.json "hourly_cost_usd", e.g. a fine-tuned deployment): informational only.
        bool showHosting = rows.Any(r => r.HourlyCostUsd > 0);
        var header = "model".PadRight(12) + "| " + "pass rate".PadLeft(9) + " | " + "avg score".PadLeft(9) + " | "
                     + "total cost".PadLeft(12) + " | " + "cost/success".PadLeft(12) + " | "
                     + "p50 ms".PadLeft(7) + " | " + "p95 ms".PadLeft(7);
        if (showHosting) header += " | " + "hosting $/h".PadLeft(11);
        var lines = new List<string> { "", header, new string('-', header.Length) };
        foreach (var row in rows)
        {
            var s = row.Summary;
            var avg = s.AvgScore.HasValue ? s.AvgScore.Value.ToString("0.00", CultureInfo.InvariantCulture) : "n/a";
            var rate = judgeEnabled ? Pct(s.PassRate, 0) : "n/a";
            var line = row.Model.PadRight(12) + "| " + rate.PadLeft(9) + " | " + avg.PadLeft(9) + " | "
                       + FmtUsd(s.TotalCostUsd).PadLeft(12) + " | " + FmtUsd(s.CostPerSuccessUsd).PadLeft(12) + " | "
                       + s.LatencyP50Ms.ToString(CultureInfo.InvariantCulture).PadLeft(7) + " | "
                       + s.LatencyP95Ms.ToString(CultureInfo.InvariantCulture).PadLeft(7);
            if (showHosting)
            {
                var hourly = row.HourlyCostUsd > 0 ? "$" + row.HourlyCostUsd.ToString("0.00", CultureInfo.InvariantCulture) : "-";
                line += " | " + hourly.PadLeft(11);
            }
            lines.Add(line);
            if (s.Errors > 0)
            {
                lines.Add(new string(' ', 12) + $"  ⚠️  {s.Errors} item(s) failed – see the JSON file for errors");
            }
        }
        if (showHosting)
        {
            lines.Add("hosting $/h = fixed hourly hosting fee from models.json (informational; not included in the "
                      + "costs above or in the score)");
        }
        Say(string.Join("\n", lines));

        var (timestamp, stamp) = UtcStamp();
        var payload = Envelope(cfg, strategy, timestamp);
        var modelNames = new JsonArray();
        var resultsJson = new JsonArray();
        foreach (var row in rows)
        {
            modelNames.Add((JsonNode?)row.Model);
            var itemsJson = new JsonArray();
            foreach (var r in row.Items) itemsJson.Add((JsonNode)r.ToJson());
            var entry = new JsonObject
            {
                ["model"] = row.Model,
                ["deployment"] = row.Deployment,
            };
            if (showHosting) entry["hourly_cost_usd"] = row.HourlyCostUsd;
            entry["summary"] = row.Summary.ToJson();
            entry["items"] = itemsJson;
            resultsJson.Add((JsonNode)entry);
        }
        payload["models"] = modelNames;
        payload["results"] = resultsJson;
        var path = WriteJson(cfg, "compare", payload, stamp);
        Say($"\n💾 Results written to {path}");
        return 0;
    }

    public static async Task<int> AskAsync(AppConfig cfg, Strategy strategy, string question, string customerId)
    {
        MockBanner(cfg);
        await PreflightAsync(cfg, strategy);
        var client = new LlmClient(cfg, strategy.UseGateway, strategy.RetryOnThrottle);
        var result = await new Pipeline(cfg, strategy, client).AnswerAsync(new QuestionInput("ask", customerId, question));
        Say($"\n❓ {question}  (customer {customerId})\n");
        Say(result.Error == null ? result.Answer : $"❌ Error: {result.Error}");
        Say();
        Say($"Model path : {(result.ModelPath.Count > 0 ? string.Join(" → ", result.ModelPath) : "-")}");
        Say($"Cache      : {result.Cache}");
        Say($"Tokens     : {N0(result.InputTokens)} in ({N0(result.CachedInputTokens)} cached) / {N0(result.OutputTokens)} out");
        foreach (var call in result.Calls)
        {
            Say($"             - {call.ModelKey}: {N0(call.PromptTokens)} in / {N0(call.CompletionTokens)} out  {FmtUsd(call.CostUsd)}");
        }
        Say($"Cost       : {FmtUsd(result.CostUsd)}");
        Say($"Latency    : {result.LatencyMs} ms");
        return result.Error != null ? 1 : 0;
    }

    public static async Task<int> DoctorAsync(AppConfig cfg, Strategy strategy)
    {
        bool ok = true;
        Say("🩺 Token Wars doctor");
        Say($"  ROOT          : {cfg.Root}");
        Say($"  App           : {cfg.Language}/{cfg.Variant}  (strategy: {cfg.StrategyPath})");
        Say($"  Team          : {cfg.Team}");
        Say($"  Mock mode     : {(cfg.Mock ? "ON" : "off")}");
        Say($"  Models from   : {cfg.ModelsSource}");
        foreach (var warning in cfg.Warnings) Say($"  ⚠️  {warning}");

        var root = cfg.Root;
        var docs = ContextBuilder.LoadKnowledgeBase(root);
        var ordersPath = Path.Combine(root, "shared", "data", "orders.json");
        var required = new List<(string Label, bool Present)>
        {
            ($"knowledge base: {docs.Count} docs / {ContextBuilder.SplitSections(docs).Count} sections", docs.Count > 0),
            ($"data/orders.json: {ContextBuilder.LoadOrders(root).Count} orders", File.Exists(ordersPath)),
            ("data/workload.jsonl", File.Exists(Path.Combine(root, "shared", "data", "workload.jsonl"))),
        };
        var optional = new[] { "prompts/system-baseline.md", "prompts/judge.md", "config/pricing.json", "config/scoring.json" };
        Say("\n  Shared data:");
        foreach (var (label, present) in required)
        {
            Say($"    {(present ? "✅" : "❌")} {label}");
            ok &= present;
        }
        foreach (var name in optional)
        {
            bool present = File.Exists(Path.Combine(root, "shared", name));
            Say($"    {(present ? "✅" : "⚠️ ")} {name}{(present ? "" : " (missing – using a built-in fallback)")}");
        }

        Say("\n  Models:");
        foreach (var (key, model) in cfg.Models)
        {
            var keyEnv = key == cfg.Scoring.JudgeModel ? model.ApiKeyEnv : cfg.Gateway?.ApiKeyEnv ?? "APIM_SUBSCRIPTION_KEY";
            var keyState = cfg.GetEnv(keyEnv).Length > 0 ? "key present" : $"key MISSING ({keyEnv})";
            Say($"    - {key,-11} {model.Deployment,-26} {model.Type,-9} via_gateway={(model.ViaGateway ? "true" : "false"),-5} {keyState}");
            if (model.Type == "chat")
            {
                var extra = model.ExtraBody.Count > 0
                    ? "{" + string.Join(", ", model.ExtraBody.Select(kv => JsonSerializer.Serialize(kv.Key) + ": " + kv.Value)) + "}"
                    : "{}";
                var details = $"{model.MaxTokensParam}, temperature={(model.SupportsTemperature ? "yes" : "omitted")}, extra_body={extra}";
                if (model.ExtraHeaders.Count > 0) // names only – values may be sensitive
                {
                    details += ", extra_headers: " + string.Join(", ", model.ExtraHeaders.Select(h => h.Key));
                }
                if (model.HourlyCostUsd > 0)
                {
                    details += ", hosting $" + model.HourlyCostUsd.ToString("0.00", CultureInfo.InvariantCulture) + "/h";
                }
                Say($"      {new string(' ', 11)} {details}");
            }
        }
        var missing = new[] { "premium", "balanced", "economy", "embedding", cfg.Scoring.JudgeModel }.Where(k => !cfg.Models.ContainsKey(k)).ToList();
        if (missing.Count > 0) Say($"    ⚠️  not configured: {string.Join(", ", missing)}");
        if (cfg.Gateway != null)
        {
            var gwKey = cfg.GetEnv(cfg.Gateway.ApiKeyEnv).Length > 0 ? "key present" : $"key MISSING ({cfg.Gateway.ApiKeyEnv})";
            Say($"    - gateway     {cfg.Gateway.BaseUrl}  {gwKey}  (use_gateway={(strategy.UseGateway ? "true" : "false")})");
        }
        else
        {
            Say("    - gateway     REQUIRED (not configured; mock mode can run without it)");
        }

        if (cfg.Mock)
        {
            Say("\n  🧪 Mock mode – skipping test calls.");
            return ok ? 0 : 1;
        }

        const int doctorMaxTokens = 64;
        Say($"\n  Test calls (chat output budget: {doctorMaxTokens} tokens each):");
        var client = new LlmClient(cfg, strategy.UseGateway, retryOnThrottle: false);
        foreach (var (key, model) in cfg.Models)
        {
            try
            {
                long latency;
                string detail;
                if (model.Type == "embedding")
                {
                    var result = await client.EmbedAsync(key, new[] { "ping" });
                    latency = result.LatencyMs;
                    detail = $"{(result.Vectors.Count > 0 ? result.Vectors[0].Length : 0)} dims";
                }
                else
                {
                    var result = await client.ChatAsync(
                        key, new List<ChatMessage> { new ChatMessage("user", "Reply with OK.") }, maxTokens: doctorMaxTokens, purpose: "doctor");
                    latency = result.LatencyMs;
                    detail = "'" + result.Text.Trim() + "'";
                }
                Say($"    ✅ {key,-11} {latency,6} ms  {detail}");
            }
            catch (Exception ex) // report every model, keep going
            {
                ok = false;
                Say($"    ❌ {key,-11} {Truncate(ex.Message, 160)}");
            }
        }
        return ok ? 0 : 1;
    }

    private static Dictionary<string, WorkItem> BuildIndex(List<WorkItem> workload)
    {
        var byId = new Dictionary<string, WorkItem>(StringComparer.Ordinal);
        foreach (var w in workload) byId[w.Id] = w;
        return byId;
    }
}
