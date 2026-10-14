using System.Diagnostics;
using System.Globalization;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TokenWars;

public sealed record ChatMessage(string Role, string Content);

public sealed class ChatResult
{
    public string ModelKey { get; init; } = "";
    public string Text { get; init; } = "";
    public CallRecord Call { get; init; } = new();
    public long LatencyMs { get; init; }
    public string? FinishReason { get; init; }
}

public sealed class EmbeddingResult
{
    public string ModelKey { get; init; } = "";
    public List<double[]> Vectors { get; init; } = new();
    public CallRecord Call { get; init; } = new();
    public long LatencyMs { get; init; }
}

/// <summary>Error from the model endpoint. For HTTP errors it carries the status and the Retry-After hints.</summary>
public sealed class LlmException : Exception
{
    public int? StatusCode { get; }
    public double? RetryAfterMs { get; }
    public double? RetryAfterSeconds { get; }

    public LlmException(string message, int? statusCode = null, double? retryAfterMs = null, double? retryAfterSeconds = null)
        : base(message)
    {
        StatusCode = statusCode;
        RetryAfterMs = retryAfterMs;
        RetryAfterSeconds = retryAfterSeconds;
    }
}

/// <summary>
/// Minimal OpenAI-compatible v1 client (chat/completions + embeddings) with raw HttpClient + System.Text.Json.
/// Works for Azure AI Foundry / Azure OpenAI (v1 API), API Management and Ollama. Has an offline mock mode.
/// </summary>
public sealed class LlmClient
{
    public const int MaxRetries = 4;
    public static readonly int[] RetryableStatus = { 429, 500, 502, 503, 504 };

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(60) };

    private readonly AppConfig _cfg;
    private readonly bool _useGateway;
    private readonly bool _retryOnThrottle;
    private static int _lengthWarningShown;

    public LlmClient(AppConfig cfg, bool useGateway, bool retryOnThrottle, bool? mock = null)
    {
        _cfg = cfg;
        _useGateway = useGateway;
        _retryOnThrottle = retryOnThrottle;
        Mock = mock ?? cfg.Mock;
    }

    public bool Mock { get; }

    public ModelConfig Model(string modelKey) => _cfg.Model(modelKey);

    /// <summary>purpose: "answer" | "classifier" | "judge" | "doctor" (only used by the mock).</summary>
    public async Task<ChatResult> ChatAsync(
        string modelKey,
        IReadOnlyList<ChatMessage> messages,
        double? temperature = null,
        int? maxTokens = null,
        bool jsonMode = false,
        string purpose = "answer",
        CancellationToken ct = default)
    {
        var model = _cfg.Model(modelKey);
        var body = new JsonObject { ["model"] = model.Deployment };
        var messageArray = new JsonArray();
        foreach (var m in messages)
        {
            messageArray.Add((JsonNode)new JsonObject { ["role"] = m.Role, ["content"] = m.Content });
        }
        body["messages"] = messageArray;
        if (temperature.HasValue && model.SupportsTemperature) body["temperature"] = temperature.Value;
        if (maxTokens.HasValue) body[model.MaxTokensParam] = maxTokens.Value;
        if (jsonMode) body["response_format"] = new JsonObject { ["type"] = "json_object" };
        foreach (var (name, rawJson) in model.ExtraBody) body[name] = JsonNode.Parse(rawJson); // e.g. reasoning_effort

        var stopwatch = Stopwatch.StartNew();
        JsonNode? data;
        if (Mock)
        {
            data = await SendAsync(() => Task.FromResult(MockChat(model, messages, purpose)), ct);
        }
        else
        {
            var (url, key) = Endpoint(model);
            var json = body.ToJsonString(JsonUtil.Compact);
            data = await SendAsync(() => PostOnceAsync(url + "chat/completions", key, json, model.ExtraHeaders, ct), ct);
        }
        long latency = stopwatch.ElapsedMilliseconds;

        string text;
        string? finishReason;
        try
        {
            var choice = data?["choices"]?[0];
            var message = choice?["message"];
            if (choice == null || message == null) throw new InvalidOperationException("no choices[0].message");
            text = JsonUtil.Str(message["content"]) ?? "";
            finishReason = JsonUtil.Str(choice["finish_reason"]);
        }
        catch (Exception ex) when (ex is InvalidOperationException or ArgumentOutOfRangeException)
        {
            throw new LlmException($"Unexpected chat response from {modelKey}: {Snippet(data)}");
        }
        if (string.IsNullOrWhiteSpace(text) && finishReason == "length")
        {
            text = ""; // recorded as an empty answer, which fails the judge
            if (purpose == "answer") WarnEmptyLength(modelKey, maxTokens);
        }

        return new ChatResult
        {
            ModelKey = modelKey,
            Text = text,
            Call = MakeCall(model, data),
            LatencyMs = latency,
            FinishReason = finishReason,
        };
    }

    public async Task<EmbeddingResult> EmbedAsync(string modelKey, IReadOnlyList<string> inputs, CancellationToken ct = default)
    {
        var model = _cfg.Model(modelKey);
        var inputArray = new JsonArray();
        foreach (var text in inputs) inputArray.Add((JsonNode?)text);
        var body = new JsonObject { ["model"] = model.Deployment, ["input"] = inputArray };

        var stopwatch = Stopwatch.StartNew();
        JsonNode? data;
        if (Mock)
        {
            data = await SendAsync(() => Task.FromResult(MockEmbeddings(inputs)), ct);
        }
        else
        {
            var (url, key) = Endpoint(model);
            var json = body.ToJsonString(JsonUtil.Compact);
            data = await SendAsync(() => PostOnceAsync(url + "embeddings", key, json, model.ExtraHeaders, ct), ct);
        }
        long latency = stopwatch.ElapsedMilliseconds;

        if (data?["data"] is not JsonArray rows)
        {
            throw new LlmException($"Unexpected embeddings response from {modelKey}: {Snippet(data)}");
        }
        var indexed = new List<(int Index, double[] Vector)>();
        foreach (var row in rows)
        {
            int index = JsonUtil.Int(row?["index"]) ?? 0;
            var values = row?["embedding"] as JsonArray
                ?? throw new LlmException($"Unexpected embeddings response from {modelKey}: {Snippet(data)}");
            var vector = new double[values.Count];
            for (int j = 0; j < values.Count; j++) vector[j] = JsonUtil.Dbl(values[j]) ?? 0;
            indexed.Add((index, vector));
        }

        return new EmbeddingResult
        {
            ModelKey = modelKey,
            // OrderBy is stable, like Python's sorted()
            Vectors = indexed.OrderBy(x => x.Index).Select(x => x.Vector).ToList(),
            Call = MakeCall(model, data),
            LatencyMs = latency,
        };
    }

    // ------------------------------------------------------------------ HTTP

    private (string Url, string Key) Endpoint(ModelConfig model)
    {
        if (model.Key != _cfg.Scoring.JudgeModel)
        {
            if (!_useGateway || !model.ViaGateway)
                throw new ConfigException($"APIM is required for \"{model.Key}\": use_gateway and via_gateway must be true.");
            var gateway = _cfg.Gateway;
            if (gateway == null || string.IsNullOrEmpty(gateway.BaseUrl))
                throw new ConfigException("APIM is required: deploy the gateway and regenerate models.json.");
            var key = _cfg.GetEnv(gateway.ApiKeyEnv);
            if (string.IsNullOrEmpty(key))
                throw new ConfigException($"APIM subscription key is missing ({gateway.ApiKeyEnv}).");
            if (model.ExtraHeaders.Select(header => header.Key).Any(name =>
                    name.Equals("api-key", StringComparison.OrdinalIgnoreCase)
                    || name.Equals("Authorization", StringComparison.OrdinalIgnoreCase)
                    || name.Equals("Ocp-Apim-Subscription-Key", StringComparison.OrdinalIgnoreCase)))
                throw new ConfigException($"Model \"{model.Key}\": extra_headers must not override gateway credentials.");
            return (gateway.BaseUrl, key);
        }
        if (model.ViaGateway)
            throw new ConfigException("The judge must use via_gateway: false; it is excluded from the team token budget.");
        if (string.IsNullOrEmpty(model.BaseUrl))
        {
            throw new LlmException($"Model \"{model.Key}\" has no base_url in models.json");
        }
        return (model.BaseUrl, _cfg.GetEnv(model.ApiKeyEnv));
    }

    /// <summary>Sends a request (HTTP or mock). With retry_on_throttle, throttled/transient errors are retried.</summary>
    private async Task<JsonNode?> SendAsync(Func<Task<JsonNode?>> sendOnce, CancellationToken ct)
    {
        if (_retryOnThrottle)
        {
            return await SendWithRetryAsync(sendOnce, ct);
        }
        return await sendOnce();
    }

    // SOLUTION 3.4 – throttle-aware retry: on 429/500/502/503/504 retry up to MaxRetries times;
    // wait = retry-after-ms (ms) or retry-after (s) if present, else exponential backoff 1s, 2s, 4s, 8s + jitter.
    private async Task<JsonNode?> SendWithRetryAsync(Func<Task<JsonNode?>> sendOnce, CancellationToken ct)
    {
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                return await sendOnce();
            }
            catch (LlmException ex) when (ex.StatusCode is int status
                                          && RetryableStatus.Contains(status)
                                          && attempt < MaxRetries)
            {
                double waitSeconds;
                if (ex.RetryAfterMs is double ms)
                {
                    waitSeconds = Math.Max(0, ms / 1000.0);
                }
                else if (ex.RetryAfterSeconds is double s)
                {
                    waitSeconds = Math.Max(0, s);
                }
                else
                {
                    waitSeconds = Math.Pow(2, attempt) + Random.Shared.NextDouble() * 0.5; // 1s, 2s, 4s, 8s + jitter
                }
                Console.Error.WriteLine(
                    $"   ↻ HTTP {ex.StatusCode} – retrying in {waitSeconds.ToString("0.0", CultureInfo.InvariantCulture)}s (retry {attempt + 1}/{MaxRetries})");
                await Task.Delay(TimeSpan.FromSeconds(waitSeconds), ct);
            }
        }
    }

    /// <summary>One HTTP POST. Throws LlmException (with status + Retry-After hints) on HTTP errors.</summary>
    private static async Task<JsonNode?> PostOnceAsync(
        string url, string apiKey, string json, IReadOnlyList<KeyValuePair<string, string>> extraHeaders, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, url);
        var content = new StringContent(json, Encoding.UTF8, "application/json");
        request.Content = content;
        // Endpoint rejects gateway credential overrides; affinity headers remain supported.
        var custom = new HashSet<string>(extraHeaders.Select(h => h.Key), StringComparer.OrdinalIgnoreCase);
        if (!string.IsNullOrEmpty(apiKey))
        {
            if (!custom.Contains("api-key")) request.Headers.TryAddWithoutValidation("api-key", apiKey);
            if (!custom.Contains("Authorization")) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        }
        foreach (var (name, value) in extraHeaders)
        {
            if (string.Equals(name, "Content-Type", StringComparison.OrdinalIgnoreCase)) continue;
            if (!request.Headers.TryAddWithoutValidation(name, value))
            {
                content.Headers.TryAddWithoutValidation(name, value); // other content headers, e.g. Content-Language
            }
        }

        using var response = await Http.SendAsync(request, ct);
        var text = await response.Content.ReadAsStringAsync(ct);
        int status = (int)response.StatusCode;

        if (status >= 400)
        {
            throw new LlmException(
                $"HTTP {status}: {Truncate(text, 300)}",
                status,
                ReadHeaderNumber(response, "retry-after-ms"),
                ReadHeaderNumber(response, "retry-after"));
        }

        try
        {
            return JsonNode.Parse(text);
        }
        catch (JsonException)
        {
            throw new LlmException($"Invalid JSON response: {Truncate(text, 300)}");
        }
    }

    /// <summary>Numeric header value (e.g. retry-after: 12), or null if missing / not a number (HTTP dates).</summary>
    private static double? ReadHeaderNumber(HttpResponseMessage response, string name)
    {
        if (response.Headers.TryGetValues(name, out var values))
        {
            var raw = values.FirstOrDefault();
            if (raw != null &&
                double.TryParse(raw.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
            {
                return value;
            }
        }
        return null;
    }

    private CallRecord MakeCall(ModelConfig model, JsonNode? data)
    {
        var usage = data?["usage"] as JsonObject;
        int prompt = JsonUtil.Int(usage?["prompt_tokens"]) ?? 0;
        int completion = JsonUtil.Int(usage?["completion_tokens"]) ?? 0;
        int cached = JsonUtil.Int((usage?["prompt_tokens_details"] as JsonObject)?["cached_tokens"]) ?? 0;
        return new CallRecord
        {
            ModelKey = model.Key,
            PromptTokens = prompt,
            CachedTokens = cached,
            CompletionTokens = completion,
            CostUsd = _cfg.Pricing.Cost(model.PricingKey, prompt, cached, completion),
        };
    }

    private static void WarnEmptyLength(string modelKey, int? maxTokens)
    {
        if (Interlocked.Exchange(ref _lengthWarningShown, 1) != 0) return;
        Console.Error.WriteLine(
            $"⚠️  {modelKey} returned an EMPTY answer (finish_reason=length, cap={(maxTokens.HasValue ? maxTokens.Value.ToString(CultureInfo.InvariantCulture) : "none")}). " +
            "Reasoning tokens count towards the output cap – raise max_output_tokens in strategy.json (or lower reasoning_effort).");
    }

    private static string Truncate(string text, int max) => text.Length > max ? text.Substring(0, max) : text;

    private static string Snippet(JsonNode? data) => Truncate(data?.ToJsonString() ?? "null", 300);

    // ------------------------------------------------------------------ mock mode (SPEC 4.6)

    private static JsonNode? MockChat(ModelConfig model, IReadOnlyList<ChatMessage> messages, string purpose)
    {
        string text;
        if (purpose == "classifier")
        {
            text = "SIMPLE";
        }
        else if (purpose == "judge")
        {
            text = "{\"score\": 5, \"reason\": \"mock\"}";
        }
        else
        {
            var lastUser = messages.LastOrDefault(m => m.Role == "user")?.Content ?? "";
            text = $"[mock:{model.Deployment}] " + TextUtil.TakeCodePoints(lastUser, 160);
        }
        int totalChars = messages.Sum(m => TextUtil.CodePointCount(m.Content));
        return new JsonObject
        {
            ["choices"] = new JsonArray(
                (JsonNode)new JsonObject
                {
                    ["message"] = new JsonObject { ["role"] = "assistant", ["content"] = text },
                    ["finish_reason"] = "stop",
                }),
            ["usage"] = new JsonObject
            {
                ["prompt_tokens"] = CeilDiv4(totalChars),
                ["completion_tokens"] = CeilDiv4(TextUtil.CodePointCount(text)),
                ["prompt_tokens_details"] = new JsonObject { ["cached_tokens"] = 0 },
            },
        };
    }

    private static JsonNode? MockEmbeddings(IReadOnlyList<string> inputs)
    {
        var rows = new JsonArray();
        for (int i = 0; i < inputs.Count; i++)
        {
            var vector = new JsonArray();
            foreach (var v in TextUtil.HashEmbedding(inputs[i])) vector.Add((JsonNode)v);
            rows.Add((JsonNode)new JsonObject { ["index"] = i, ["embedding"] = vector });
        }
        int totalChars = inputs.Sum(t => TextUtil.CodePointCount(t));
        return new JsonObject
        {
            ["data"] = rows,
            ["usage"] = new JsonObject { ["prompt_tokens"] = CeilDiv4(totalChars) },
        };
    }

    private static int CeilDiv4(int chars) => (chars + 3) / 4;
}
