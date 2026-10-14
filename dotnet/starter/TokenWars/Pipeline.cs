using System.Diagnostics;
using System.Text.Json.Nodes;

namespace TokenWars;

/// <summary>The only workload fields the answering pipeline may see (the rest is judge-only ground truth).</summary>
public sealed record QuestionInput(string Id, string CustomerId, string Question);

/// <summary>Result + accounting for one answered item.</summary>
public sealed class ItemResult
{
    public string Id { get; init; } = "";
    public string Question { get; init; } = "";
    public List<string> ModelPath { get; } = new();
    public string Cache { get; set; } = "none"; // none | exact | semantic
    public string Answer { get; set; } = "";
    public int? JudgeScore { get; set; }
    public string? JudgeReason { get; set; }
    public bool Success { get; set; }
    public double CostUsd { get; set; }
    public int InputTokens { get; set; }
    public int CachedInputTokens { get; set; }
    public int OutputTokens { get; set; }
    public long LatencyMs { get; set; }
    public string? Error { get; set; }
    public List<CallRecord> Calls { get; } = new();

    public int Escalations => Math.Max(ModelPath.Count - 1, 0);

    public JsonObject ToJson()
    {
        var path = new JsonArray();
        foreach (var m in ModelPath) path.Add((JsonNode?)m);
        return new JsonObject
        {
            ["id"] = Id,
            ["question"] = Question,
            ["model_path"] = path,
            ["cache"] = Cache,
            ["answer"] = Answer,
            ["judge_score"] = JudgeScore,
            ["judge_reason"] = JudgeReason,
            ["success"] = Success,
            ["cost_usd"] = JsonUtil.Money(CostUsd),
            ["input_tokens"] = InputTokens,
            ["cached_input_tokens"] = CachedInputTokens,
            ["output_tokens"] = OutputTokens,
            ["latency_ms"] = LatencyMs,
            ["error"] = Error,
        };
    }
}

/// <summary>The answer pipeline for one workload item (SPEC 4.2).</summary>
public sealed class Pipeline
{
    /// <summary>
    /// Next model tier when a cheaper model answers ESCALATE (TODO 3.3).
    /// Any other non-premium key (fw, custom, ...) escalates straight to "premium".
    /// </summary>
    public static readonly Dictionary<string, string> EscalationNext = new(StringComparer.Ordinal)
    {
        ["economy"] = "balanced",
        ["balanced"] = "premium",
        ["open"] = "premium",
        ["selfhosted"] = "premium",
    };

    private readonly AppConfig _cfg;
    private readonly Strategy _strategy;
    private readonly LlmClient _client;
    private readonly ResponseCache _cache;
    private readonly string _template;
    private readonly ContextBuilder _context;
    private readonly Router _router;

    public Pipeline(AppConfig cfg, Strategy strategy, LlmClient client, ResponseCache? cache = null,
                    bool persistEmbeddings = true)
    {
        _cfg = cfg;
        _strategy = strategy;
        _client = client;
        _cache = cache ?? new ResponseCache();
        _template = Prompts.LoadBaselineTemplate(cfg.Root);
        var embeddingsFile = persistEmbeddings ? Path.Combine(cfg.ResultsDir, ".kb-embeddings.json") : null;
        _context = new ContextBuilder(cfg.Root, strategy, client, embeddingsFile);
        _router = new Router(strategy, client);
        cfg.Model(strategy.DefaultModel); // fail early on an unknown default_model
        if (strategy.Routing != "none")
        {
            foreach (var tierModel in Router.TierModels.Values) cfg.Model(tierModel); // any key from models.json works, typos fail early
        }
    }

    // TODO 1.4 – Output control: max tokens + focused style (Challenge 1 "The Token Diet")
    // Output tokens cost 4x input tokens, and the baseline lets the model write as much as it likes.
    // When _strategy.MaxOutputTokens is set, return (0.2, _strategy.MaxOutputTokens.Value): the client then sends
    // the limit as max_tokens / max_completion_tokens (see "max_tokens_param" in models.json) and the lower
    // temperature keeps answers focused. Combine it with the compact prompt (TODO 1.1) so that answers are short
    // by design instead of being cut off mid-sentence. Then set "max_output_tokens" (e.g. 350) in strategy.json.
    /// <summary>Returns (temperature, max_tokens) for answer calls.</summary>
    public (double Temperature, int? MaxTokens) GenerationSettings()
    {
        if (_strategy.MaxOutputTokens.HasValue)
        {
            throw new NotImplementedException("TODO 1.4 not implemented yet – see Pipeline.cs");
        }
        return (0.7, null); // baseline: creative temperature, no output limit
    }

    /// <summary>Only Id, CustomerId and Question may be used here (ground truth is for the judge only).</summary>
    public async Task<ItemResult> AnswerAsync(QuestionInput item, CancellationToken ct = default)
    {
        var result = new ItemResult { Id = item.Id, Question = item.Question };
        var calls = new List<CallRecord>();
        var stopwatch = Stopwatch.StartNew();
        try
        {
            await AnswerCoreAsync(item.Question, item.CustomerId ?? "", result, calls, ct);
        }
        catch (NotImplementedException)
        {
            throw;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex) // recorded as a failed item
        {
            result.Error = $"{ex.GetType().Name}: {ex.Message}";
            result.Answer = "";
        }
        finally
        {
            result.LatencyMs = stopwatch.ElapsedMilliseconds;
            Account(result, calls);
        }
        return result;
    }

    private async Task AnswerCoreAsync(
        string question, string customerId, ItemResult result, List<CallRecord> calls, CancellationToken ct)
    {
        // 1-2. normalise + detect order-specific questions
        var normalized = TextUtil.Normalize(question);
        bool orderSpecific = TextUtil.IsOrderSpecific(question);

        double[]? memo = null;
        async Task<double[]> QuestionVector()
        {
            if (memo == null)
            {
                var embedded = await _client.EmbedAsync("embedding", new[] { normalized }, ct);
                calls.Add(embedded.Call);
                memo = embedded.Vectors[0];
            }
            return memo;
        }

        // 3. exact cache
        string? exactKey = null;
        if (_strategy.ExactCache)
        {
            exactKey = ResponseCache.ExactKey(normalized, customerId, orderSpecific);
            var cached = _cache.GetExact(exactKey);
            if (cached != null)
            {
                result.Cache = "exact";
                result.Answer = cached;
                return;
            }
        }

        // 4. semantic cache (never for order-specific questions)
        bool useSemantic = _strategy.SemanticCache && !orderSpecific;
        if (useSemantic)
        {
            var cached = _cache.GetSemantic(await QuestionVector(), _strategy.SemanticCacheThreshold);
            if (cached != null)
            {
                result.Cache = "semantic";
                result.Answer = cached;
                return;
            }
        }

        // 5. routing
        var modelKey = await _router.RouteAsync(question, normalized, orderSpecific, calls, ct);
        result.ModelPath.Add(modelKey);

        // 6-7. context + model call
        var answer = await CallModelAsync(modelKey, question, customerId, orderSpecific, calls, QuestionVector, ct);

        // 8. TODO 3.3 – Escalation on ESCALATE (Challenge 3 "Route & Rule")
        // With escalation=true, non-premium models are told to reply with the single word ESCALATE when unsure
        // (see Prompts.EscalateInstruction). Implement the safety net:
        //   while modelKey != "premium" and answer.Trim() starts with "ESCALATE" (upper-cased):
        //       next = EscalationNext.TryGetValue(modelKey, out var mapped) ? mapped : "premium";
        //              // keys not in the map (fw, custom, ...) escalate straight to premium
        //       if next is not configured (not in _cfg.Models): break;
        //       modelKey = next; result.ModelPath.Add(modelKey);
        //       answer = await CallModelAsync(modelKey, question, customerId, orderSpecific, calls, QuestionVector, ct);
        // Then set "escalation": true in strategy.json (cheap first, expensive only when needed).
        if (_strategy.Escalation)
        {
            throw new NotImplementedException("TODO 3.3 not implemented yet – see Pipeline.cs");
        }

        result.Answer = answer;

        // 9. store in caches (never cache an empty answer)
        if (string.IsNullOrWhiteSpace(answer)) return;
        if (exactKey != null)
        {
            _cache.PutExact(exactKey, answer);
        }
        if (useSemantic)
        {
            _cache.PutSemantic(await QuestionVector(), answer);
        }
    }

    private async Task<string> CallModelAsync(
        string modelKey, string question, string customerId, bool orderSpecific, List<CallRecord> calls,
        Func<Task<double[]>> questionVector, CancellationToken ct)
    {
        var kbText = await _context.KbContextAsync(question, calls, questionVector, ct);
        var orders = _context.OrdersContext(question, customerId, orderSpecific);
        var ordersJson = orders.Count > 0 ? ContextBuilder.OrdersToJson(orders) : "";
        var messages = Prompts.BuildMessages(_strategy, _template, modelKey, question, customerId, kbText, ordersJson);
        var (temperature, maxTokens) = GenerationSettings();
        var response = await _client.ChatAsync(modelKey, messages, temperature, maxTokens, ct: ct);
        calls.Add(response.Call);
        return response.Text;
    }

    private static void Account(ItemResult result, List<CallRecord> calls)
    {
        foreach (var call in calls)
        {
            result.Calls.Add(call);
            result.CostUsd += call.CostUsd;
            result.InputTokens += call.PromptTokens;
            result.CachedInputTokens += call.CachedTokens;
            result.OutputTokens += call.CompletionTokens;
        }
    }
}
