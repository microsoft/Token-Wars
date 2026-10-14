using System.Collections.Concurrent;
using System.Text.Json.Nodes;

namespace TokenWars;

public sealed record PriceEntry(double InputPer1M, double CachedInputPer1M, double OutputPer1M);

/// <summary>shared/config/pricing.json – list prices per 1M tokens (SPEC 2.3, 4.7).</summary>
public sealed class PricingTable
{
    private readonly Dictionary<string, PriceEntry> _prices;
    private readonly ConcurrentDictionary<string, bool> _warned = new();

    public string Currency { get; }

    private PricingTable(string currency, Dictionary<string, PriceEntry> prices)
    {
        Currency = currency;
        _prices = prices;
    }

    public static PricingTable Empty() => new("USD", new Dictionary<string, PriceEntry>());

    /// <summary>Short-context prices used when pricing.json is missing; Sol's input/output promotion runs through at least 2026-11-30.</summary>
    public static PricingTable BuiltIn() => new("USD", new Dictionary<string, PriceEntry>(StringComparer.Ordinal)
    {
        ["gpt-5.5"] = new PriceEntry(5.00, 0.50, 30.00),
        ["gpt-5.6-sol"] = new PriceEntry(4.00, 0.50, 20.00),
        ["gpt-5.6-terra"] = new PriceEntry(2.00, 0.20, 12.00),
        ["gpt-5.6-luna"] = new PriceEntry(0.20, 0.02, 1.20),
        ["gpt-5.4"] = new PriceEntry(2.50, 0.25, 15.00),
        ["gpt-5.4-mini"] = new PriceEntry(0.75, 0.075, 4.50),
        ["gpt-5.4-nano"] = new PriceEntry(0.20, 0.02, 1.25),
        ["gpt-4.1"] = new PriceEntry(2.00, 0.50, 8.00),
        ["gpt-4.1-mini"] = new PriceEntry(0.40, 0.10, 1.60),
        ["gpt-4.1-nano"] = new PriceEntry(0.10, 0.025, 0.40),
        ["llama-3.3-70b-instruct"] = new PriceEntry(0.71, 0.71, 0.71),
        ["selfhosted"] = new PriceEntry(0.0, 0.0, 0.0),
        ["text-embedding-3-small"] = new PriceEntry(0.02, 0.02, 0.0),
    });

    public static PricingTable Load(string path)
    {
        var doc = JsonUtil.ParseFile(path);
        var prices = new Dictionary<string, PriceEntry>(StringComparer.Ordinal);
        if (doc?["models"] is JsonObject models)
        {
            foreach (var (key, node) in models)
            {
                if (node is not JsonObject p) continue;
                var input = JsonUtil.Dbl(p["input_per_1m"]) ?? 0;
                prices[key] = new PriceEntry(
                    input,
                    JsonUtil.Dbl(p["cached_input_per_1m"]) ?? input,
                    JsonUtil.Dbl(p["output_per_1m"]) ?? 0);
            }
        }
        return new PricingTable(JsonUtil.Str(doc?["currency"]) ?? "USD", prices);
    }

    public bool Has(string pricingKey) => _prices.ContainsKey(pricingKey);

    /// <summary>((prompt - cached) * input + cached * cached_input + completion * output) / 1M</summary>
    public double Cost(string pricingKey, int promptTokens, int cachedTokens, int completionTokens)
    {
        if (!_prices.TryGetValue(pricingKey, out var p))
        {
            if (_warned.TryAdd(pricingKey, true))
            {
                Console.Error.WriteLine($"⚠️  No price for \"{pricingKey}\" in pricing.json – counting its calls as $0.");
            }
            return 0;
        }
        int uncached = Math.Max(0, promptTokens - cachedTokens);
        return (uncached * p.InputPer1M + cachedTokens * p.CachedInputPer1M + completionTokens * p.OutputPer1M) / 1_000_000.0;
    }
}

/// <summary>One billed LLM call (chat or embeddings).</summary>
public sealed class CallRecord
{
    public string ModelKey { get; init; } = "";
    public int PromptTokens { get; init; }
    public int CachedTokens { get; init; }
    public int CompletionTokens { get; init; }
    public double CostUsd { get; init; }
}

/// <summary>Aggregated usage for one model key (calls_by_model in the summary).</summary>
public sealed class ModelUsage
{
    public int Calls { get; set; }
    public long InputTokens { get; set; }
    public long CachedInputTokens { get; set; }
    public long OutputTokens { get; set; }
    public double CostUsd { get; set; }

    public JsonObject ToJson() => new()
    {
        ["calls"] = Calls,
        ["input_tokens"] = InputTokens,
        ["cached_input_tokens"] = CachedInputTokens,
        ["output_tokens"] = OutputTokens,
        ["cost_usd"] = JsonUtil.Money(CostUsd),
    };

    public static SortedDictionary<string, ModelUsage> ByModel(IEnumerable<CallRecord> calls)
    {
        var result = new SortedDictionary<string, ModelUsage>(StringComparer.Ordinal);
        foreach (var c in calls)
        {
            if (!result.TryGetValue(c.ModelKey, out var u))
            {
                u = new ModelUsage();
                result[c.ModelKey] = u;
            }
            u.Calls++;
            u.InputTokens += c.PromptTokens;
            u.CachedInputTokens += c.CachedTokens;
            u.OutputTokens += c.CompletionTokens;
            u.CostUsd += c.CostUsd;
        }
        return result;
    }
}
