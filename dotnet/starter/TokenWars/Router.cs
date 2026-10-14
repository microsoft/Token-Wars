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

    // TODO 3.1 – Rules router (Challenge 3 "Route & Rule")
    // Most questions do not need the premium model. Route deterministically, in this order:
    //   - orderSpecific                                                           -> TierModels["STANDARD"] (balanced)
    //   - question.Trim() longer than LongQuestionChars (use TextUtil.CodePointCount)
    //     or >= 2 ComplexityMarkers                                               -> TierModels["COMPLEX"]  (premium)
    //   - any ComplaintWords                                                      -> TierModels["STANDARD"] (balanced)
    //   - otherwise                                                               -> TierModels["SIMPLE"]   (economy)
    // Match markers/words on `normalized` with ContainsPhrase() so "but" does not match "button".
    // Then set "routing": "rules" in strategy.json.
    public string RouteRules(string question, string normalized, bool orderSpecific)
    {
        throw new NotImplementedException("TODO 3.1 not implemented yet – see Router.cs");
    }

    // TODO 3.2 – Classifier router (Challenge 3 "Route & Rule")
    // Let the cheapest model decide: make this method `async` and call
    //   await _client.ChatAsync("economy", messages, temperature: 0, maxTokens: 5, purpose: "classifier", ct: ct)
    // with system = Prompts.ClassifierSystemPrompt and user = question.
    // Add result.Call to `calls` (the classifier tokens are part of your bill!), take the first word of result.Text
    // (upper-case, without punctuation such as . , : ; ! " ' ` *) and map it with TierModels;
    // anything else -> _strategy.DefaultModel.
    // Then set "routing": "classifier" in strategy.json.
    public Task<string> RouteClassifierAsync(string question, List<CallRecord> calls, CancellationToken ct = default)
    {
        throw new NotImplementedException("TODO 3.2 not implemented yet – see Router.cs");
    }
}
