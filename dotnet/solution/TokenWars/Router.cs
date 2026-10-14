using System.Text.RegularExpressions;

namespace TokenWars;

/// <summary>Model routing: none (default_model), rules (TODO 3.1) or an economy classifier (TODO 3.2).</summary>
public sealed class Router
{
    /// <summary>Challenge 2.2: after `compare`, you may change which model serves each tier.</summary>
    public static readonly Dictionary<string, string> TierModels = new(StringComparer.Ordinal)
    {
        ["SIMPLE"] = "economy",
        ["STANDARD"] = "balanced",
        ["COMPLEX"] = "premium",
    };

    public static readonly string[] ComplexityMarkers =
    {
        "but", "however", "although", "both", "and also", "what if", "exception", "combine", "at the same time", "instead",
    };

    public static readonly string[] ComplaintWords =
    {
        "angry", "unacceptable", "disappointed", "terrible", "worst", "still haven't", "complaint",
    };

    public const int LongQuestionChars = 280;

    private readonly Strategy _strategy;
    private readonly LlmClient _client;

    public Router(Strategy strategy, LlmClient client)
    {
        _strategy = strategy;
        _client = client;
    }

    /// <summary>Whole-word / whole-phrase match, so "but" does not match "button".</summary>
    public static bool ContainsPhrase(string text, string phrase) =>
        Regex.IsMatch(text, @"\b" + Regex.Escape(phrase) + @"\b", RegexOptions.CultureInvariant);

    public async Task<string> RouteAsync(
        string question, string normalized, bool orderSpecific, List<CallRecord> calls, CancellationToken ct = default)
    {
        if (_strategy.Routing == "rules") return RouteRules(question, normalized, orderSpecific);
        if (_strategy.Routing == "classifier") return await RouteClassifierAsync(question, calls, ct);
        return _strategy.DefaultModel;
    }

    // SOLUTION 3.1 – deterministic rules router.
    public string RouteRules(string question, string normalized, bool orderSpecific)
    {
        if (orderSpecific) return TierModels["STANDARD"];
        int markers = ComplexityMarkers.Count(m => ContainsPhrase(normalized, m));
        if (TextUtil.CodePointCount(question.Trim()) > LongQuestionChars || markers >= 2) return TierModels["COMPLEX"];
        if (ComplaintWords.Any(w => ContainsPhrase(normalized, w))) return TierModels["STANDARD"];
        return TierModels["SIMPLE"];
    }

    // SOLUTION 3.2 – classifier router: ask economy for SIMPLE / STANDARD / COMPLEX (its tokens are costed).
    public async Task<string> RouteClassifierAsync(string question, List<CallRecord> calls, CancellationToken ct = default)
    {
        var messages = new List<ChatMessage>
        {
            new ChatMessage("system", Prompts.ClassifierSystemPrompt),
            new ChatMessage("user", question),
        };
        var result = await _client.ChatAsync("economy", messages, temperature: 0, maxTokens: 5, purpose: "classifier", ct: ct);
        calls.Add(result.Call);

        var words = result.Text.Trim().ToUpperInvariant()
            .Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        var label = words.Length > 0 ? words[0].Trim('.', ',', ':', ';', '!', '"', '\'', '`', '*') : "";
        return TierModels.TryGetValue(label, out var model) ? model : _strategy.DefaultModel;
    }
}
