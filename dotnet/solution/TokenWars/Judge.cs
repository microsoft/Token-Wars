using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace TokenWars;

public sealed record Verdict(int Score, string Reason, double CostUsd);

/// <summary>LLM-as-a-judge scoring (SPEC 3.4 / 4.8). Judge cost is tracked separately from the team score.</summary>
public sealed class Judge
{
    private const int JudgeRetries = 4;

    private const string FallbackJudge =
        "You are grading a customer support answer for ByteCart.\n" +
        "Question: {{question}}\n" +
        "Reference answer: {{reference_answer}}\n" +
        "Facts that must be included: {{must_include}}\n" +
        "Answer to grade: {{answer}}\n" +
        "\n" +
        "Score 1-5: 5 = correct & complete; 4 = correct, minor omissions, all must-include facts present;\n" +
        "3 = partially correct or missing a must-include fact; 2 = mostly wrong; 1 = wrong/hallucinated/harmful.\n" +
        "Verbosity is neither rewarded nor penalised. \"I'll escalate to a human\" without answering = max 2.\n" +
        "Output ONLY JSON: {\"score\": <1-5>, \"reason\": \"<one sentence>\"}";

    private static readonly Regex DigitRegex = new("[1-5]", RegexOptions.CultureInvariant);

    private readonly LlmClient _client;
    private readonly string _template;
    private readonly string _knowledgeBase;
    private readonly List<JsonNode> _orders;

    public Judge(AppConfig cfg, LlmClient client)
    {
        _client = client;
        ModelKey = cfg.Scoring.JudgeModel;
        if (!cfg.Models.ContainsKey(ModelKey))
        {
            throw new ConfigException($"Judge model \"{ModelKey}\" is not configured; an independent direct judge is required.");
        }
        var path = cfg.SharedPath("prompts", "judge.md");
        var model = cfg.Model(ModelKey);
        if (model.ViaGateway || model.Type != "chat")
            throw new ConfigException("The independent judge must be a chat model with via_gateway: false.");
        _template = File.Exists(path) ? File.ReadAllText(path).Replace("\r\n", "\n") : FallbackJudge;
        _knowledgeBase = string.Join("\n\n", ContextBuilder.LoadKnowledgeBase(cfg.Root).Select(d => d.Content));
        _orders = ContextBuilder.LoadOrders(cfg.Root);
    }

    public string ModelKey { get; }

    public static (int Score, string Reason) ParseVerdict(string text)
    {
        text ??= "";
        try
        {
            if (JsonNode.Parse(text) is JsonObject data)
            {
                int? score = JsonUtil.Int(data["score"]);
                if (score is int s && s >= 1 && s <= 5)
                {
                    var reasonNode = data["reason"];
                    string reason = reasonNode == null
                        ? ""
                        : JsonUtil.Str(reasonNode) ?? reasonNode.ToJsonString();
                    return (s, reason);
                }
            }
        }
        catch (JsonException)
        {
            // not JSON – fall through to the digit fallback
        }

        var match = DigitRegex.Match(text);
        if (match.Success)
        {
            return (int.Parse(match.Value), Truncate(text.Trim(), 200));
        }
        return (1, "unparseable judge output: " + Truncate(text.Trim(), 160));
    }

    public async Task<Verdict> JudgeAsync(string question, string referenceAnswer, IReadOnlyList<string> mustInclude, string answer, string customerId = "")
    {
        var mustText = string.Join("; ", mustInclude);
        var customerOrders = _orders
            .Where(o => (o["customer_id"]?.GetValue<string>() ?? "") == customerId)
            .ToList();
        var ordersText = customerOrders.Count > 0 ? ContextBuilder.OrdersToJson(customerOrders) : "(no orders)";
        var prompt = _template
            .Replace("{{knowledge_base}}", _knowledgeBase)
            .Replace("{{customer_orders}}", ordersText)
            .Replace("{{question}}", question)
            .Replace("{{reference_answer}}", referenceAnswer ?? "")
            .Replace("{{must_include}}", mustText.Length > 0 ? mustText : "(none)")
            .Replace("{{answer}}", answer);
        var messages = new List<ChatMessage>
        {
            new ChatMessage("system", prompt),
            new ChatMessage("user", "Grade the answer now. Output only the JSON object."),
        };

        // The judge retries throttling on its own, so scoring never depends on TODO 3.4.
        ChatResult? result = null;
        for (int attempt = 0; attempt <= JudgeRetries; attempt++)
        {
            try
            {
                result = await _client.ChatAsync(ModelKey, messages, temperature: 0, jsonMode: true, purpose: "judge");
                break;
            }
            catch (LlmException ex) when (ex.StatusCode is int status
                                          && LlmClient.RetryableStatus.Contains(status)
                                          && attempt < JudgeRetries)
            {
                await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt + 1) + Random.Shared.NextDouble()));
            }
            catch (NotImplementedException)
            {
                throw;
            }
            catch (Exception ex) // judge failures should not crash the run
            {
                return new Verdict(1, Truncate($"judge error: {ex.Message}", 200), 0.0);
            }
        }
        if (result == null)
        {
            return new Verdict(1, "judge error: no response", 0.0);
        }

        var (score, reason) = ParseVerdict(result.Text);
        return new Verdict(score, reason, result.Call.CostUsd);
    }

    private static string Truncate(string text, int max) => text.Length > max ? text.Substring(0, max) : text;
}
