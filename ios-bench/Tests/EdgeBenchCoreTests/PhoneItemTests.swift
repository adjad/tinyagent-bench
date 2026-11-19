import XCTest
@testable import EdgeBenchCore

final class PhoneItemTests: XCTestCase {
    // The spec's example item (§6.3) with the "…" placeholders filled in.
    static let specItem = #"""
    {"schema": "edat-phone-item/v1",
     "id": "ph-seq-0012", "source_case": "seq_move_dentist_tell_partner", "family": "sequential",
     "lane": "harbor", "world_seed": 3, "today": "2026-11-18", "tz": "America/Los_Angeles",
     "thinking": false,
     "sampling": {"source": "author_recommended", "temperature": 1.0, "top_p": 0.95, "top_k": 64, "seed": 1003, "max_tokens": 512},
     "messages": [
       {"role": "system", "content": "You are a helpful assistant."},
       {"role": "user", "content": "push my dentist appointment back an hour and let sam know the new time"},
       {"role": "assistant", "content": "", "tool_calls": [
         {"id": "c1", "type": "function", "function": {"name": "get_upcoming", "arguments": "{\"days\": 7}"}}]},
       {"role": "tool", "tool_call_id": "c1",
        "content": "Thu 20 Nov 10:00-10:45  Dentist - Dr. Okafor  (id ev-5521)"}],
     "tools": [{"type": "function", "function": {"name": "update_event", "description": "Move an event.", "parameters": {"type": "object"}}}],
     "rendered_prompt": "<bos><start_of_turn>system", "rendered_prompt_sha256": "abc123",
     "check": {"kind": "calls", "same_step": true, "match": "all_any_order", "extra_calls": "allowed",
               "calls": [{"name": "update_event",
                          "args": {"event_id": {"eq": "ev-5521"}, "start": {"regex": "T11:00"}}}],
               "forbid": ["cancel_event", "add_calendar_event"]},
     "text": null}
    """#

    func make(_ check: String, text: String = "null", today: String = "2026-11-18") throws -> PhoneItem {
        let json = #"{"schema":"edat-phone-item/v1","id":"t1","family":"select","today":"\#(today)","thinking":true,"#
            + #""sampling":{"source":"fixed_sensitivity","temperature":0.6,"top_p":0.95,"top_k":null,"seed":7,"max_tokens":64},"#
            + #""messages":[{"role":"user","content":"hi"}],"tools":[],"check":\#(check),"text":\#(text)}"#
        return try JSONDecoder().decode(PhoneItem.self, from: Data(json.utf8))
    }

    func call(_ name: String, _ raw: String = "{}") -> PhoneParsedCall {
        PhoneParsedCall(name: name, argumentsRaw: raw, arguments: nil)
    }

    func spec() throws -> PhoneItem { try JSONDecoder().decode(PhoneItem.self, from: Data(Self.specItem.utf8)) }

    // MARK: decoding

    func testDecodesSpecExample() throws {
        let item = try spec()
        XCTAssertEqual(item.id, "ph-seq-0012")
        XCTAssertEqual(item.sourceCase, "seq_move_dentist_tell_partner")
        XCTAssertEqual(item.worldSeed, 3)
        XCTAssertFalse(item.thinking)
        XCTAssertNil(item.text)
        XCTAssertEqual(item.renderedPromptSHA256, "abc123")
        XCTAssertEqual(item.sampling.seed, 1003)
        XCTAssertEqual(item.sampling.sampling.topK, 64)
        XCTAssertEqual(item.sampling.maxTokens, 512)
        XCTAssertEqual(item.todayComponents?.day, 18)
        XCTAssertEqual(item.check.forbid, ["cancel_event", "add_calendar_event"])
        let msgs = item.benchMessages
        XCTAssertEqual(msgs.count, 4)
        XCTAssertEqual(msgs[2].toolCalls?.first?["function"]?["name"]?.stringValue, "get_upcoming")
        XCTAssertEqual(msgs[3].toolCallID, "c1")
    }

    func testSpecExampleGrading() throws {
        let item = try spec()
        let good = call("update_event", #"{"event_id":"ev-5521","start":"2026-11-20T11:00:00"}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [good]).pass)

        let forbidden = PhoneGrader.grade(item, answer: "", calls: [good, call("cancel_event")])
        XCTAssertFalse(forbidden.pass)
        XCTAssertTrue(forbidden.labels.contains("forbidden call cancel_event"))

        let wrong = PhoneGrader.grade(item, answer: "", calls: [call("update_event", #"{"event_id":"ev-5521","start":"2026-11-20T12:00:00"}"#)])
        XCTAssertFalse(wrong.pass)
        XCTAssertTrue(wrong.labels.contains("missing call update_event"))
        XCTAssertTrue(wrong.labels.contains { $0.hasPrefix("arg start") })

        XCTAssertFalse(PhoneGrader.grade(item, answer: "done", calls: []).pass)
    }

    func testArgumentsUseParsedObjectWhenPresent() throws {
        let item = try make(#"{"kind":"calls","calls":[{"name":"f","args":{"x":{"eq":"1"}}}]}"#)
        let parsed = PhoneParsedCall(name: "f", argumentsRaw: "not json", arguments: .object(["x": .string("1")]))
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [parsed]).pass)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "", calls: [call("f", "not json")]).pass)
    }

    // MARK: call matching

    func testAllAnyOrder() throws {
        let item = try make(#"{"kind":"calls","match":"all_any_order","calls":[{"name":"a"},{"name":"b","args":{"n":{"eq":"2"}}}]}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [call("b", #"{"n":2}"#), call("a")]).pass)
        let missing = PhoneGrader.grade(item, answer: "", calls: [call("a")])
        XCTAssertFalse(missing.pass)
        XCTAssertEqual(missing.labels, ["missing call b"])
    }

    func testAllAnyOrderNeedsDistinctCalls() throws {
        let item = try make(#"{"kind":"calls","calls":[{"name":"a"},{"name":"a"}]}"#)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "", calls: [call("a")]).pass)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [call("a"), call("a")]).pass)
    }

    func testAnyNeedsOneMatch() throws {
        let item = try make(#"{"kind":"calls","match":"any","calls":[{"name":"a"},{"name":"b"}]}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [call("b")]).pass)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "", calls: [call("c")]).pass)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "", calls: []).pass)
    }

    func testExtraCallsForbidden() throws {
        let strict = try make(#"{"kind":"calls","extra_calls":"forbidden","calls":[{"name":"a"}]}"#)
        let r = PhoneGrader.grade(strict, answer: "", calls: [call("a"), call("z")])
        XCTAssertFalse(r.pass)
        XCTAssertEqual(r.labels, ["extra call z"])
        XCTAssertTrue(PhoneGrader.grade(strict, answer: "", calls: [call("a")]).pass)
        let loose = try make(#"{"kind":"calls","extra_calls":"allowed","calls":[{"name":"a"}]}"#)
        XCTAssertTrue(PhoneGrader.grade(loose, answer: "", calls: [call("a"), call("z")]).pass)
    }

    func testDateMatcherIsRelativeToItemToday() throws {
        let check = #"{"kind":"calls","calls":[{"name":"r","args":{"when":{"date_offset":1}}}]}"#
        let a = try make(check, today: "2026-11-18")
        XCTAssertTrue(PhoneGrader.grade(a, answer: "", calls: [call("r", #"{"when":"2026-11-19T16:00:00"}"#)]).pass)
        XCTAssertFalse(PhoneGrader.grade(a, answer: "", calls: [call("r", #"{"when":"2026-11-20T16:00:00"}"#)]).pass)
        let b = try make(check, today: "2026-12-31")
        XCTAssertTrue(PhoneGrader.grade(b, answer: "", calls: [call("r", #"{"when":"2027-01-01T09:00:00"}"#)]).pass)
        let wd = try make(#"{"kind":"calls","calls":[{"name":"r","args":{"when":{"weekday":"thursday"}}}]}"#)
        XCTAssertTrue(PhoneGrader.grade(wd, answer: "", calls: [call("r", #"{"when":"2026-11-19"}"#)]).pass)
    }

    // MARK: no_call, text, either

    func testNoCallWithText() throws {
        let item = try make(#"{"kind":"no_call"}"#,
                            text: #"{"contains_all":["sorry","later"],"contains_any":["retry","again"],"not_contains":["success"]}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "Sorry, it failed. Try again LATER.", calls: []).pass)
        let bad = PhoneGrader.grade(item, answer: "Success! Sorry.", calls: [])
        XCTAssertFalse(bad.pass)
        XCTAssertEqual(bad.labels.count, 3)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "sorry later again", calls: [call("a")]).pass)
    }

    func testRegexIsCaseInsensitive() throws {
        let item = try make(#"{"kind":"no_call"}"#, text: #"{"regex":"\\bcould not\\b"}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "I COULD NOT do that.", calls: []).pass)
        XCTAssertFalse(PhoneGrader.grade(item, answer: "Done.", calls: []).pass)
    }

    func testEither() throws {
        let item = try make(#"{"kind":"either","options":[{"kind":"calls","calls":[{"name":"retry"}]},{"kind":"no_call","text":{"contains_any":["unable"]}}]}"#,
                            text: #"{"not_contains":["booked"]}"#)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "", calls: [call("retry")]).pass)
        XCTAssertTrue(PhoneGrader.grade(item, answer: "I am unable to.", calls: []).pass)
        let neither = PhoneGrader.grade(item, answer: "All good", calls: [call("other")])
        XCTAssertFalse(neither.pass)
        XCTAssertTrue(neither.labels.contains { $0.hasPrefix("option 1:") } && neither.labels.contains { $0.hasPrefix("option 2:") })
        XCTAssertFalse(PhoneGrader.grade(item, answer: "booked", calls: [call("retry")]).pass)   // top-level text still applies
    }

    // MARK: loading

    func testLoadReportsLineNumber() throws {
        let good = Self.specItem.replacingOccurrences(of: "\n", with: " ")
        let jsonl = Data((good + "\n\n" + #"{"schema":"edat-phone-item/v1","id":"oops"}"# + "\n").utf8)
        XCTAssertThrowsError(try PhoneItemSet.load(jsonl: jsonl)) { error in
            guard case PhoneItemError.badLine(let line, _) = error else { return XCTFail("\(error)") }
            XCTAssertEqual(line, 3)
        }
        let wrong = Data(good.replacingOccurrences(of: "edat-phone-item/v1", with: "edat-phone-item/v9").utf8)
        XCTAssertThrowsError(try PhoneItemSet.load(jsonl: wrong)) { error in
            guard case PhoneItemError.badLine(let line, _) = error else { return XCTFail("\(error)") }
            XCTAssertEqual(line, 1)
        }
    }

    func testLoadSkipsBlankLinesAndHashesFile() throws {
        let good = Self.specItem.replacingOccurrences(of: "\n", with: " ")
        let jsonl = Data(("\n" + good + "\n  \n").utf8)
        let set = try PhoneItemSet.load(jsonl: jsonl)
        XCTAssertEqual(set.items.count, 1)
        XCTAssertEqual(set.sha256.count, 64)
        XCTAssertNil(set.manifest)
    }

    func testStandInFileMatchesManifest() throws {
        let resources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Resources")
        let jsonl = try Data(contentsOf: resources.appendingPathComponent("phone-items-standin.jsonl"))
        let manifest = try Data(contentsOf: resources.appendingPathComponent("phone-items-standin.manifest.json"))
        let set = try PhoneItemSet.load(jsonl: jsonl, manifest: manifest)
        XCTAssertEqual(set.items.count, set.manifest?.itemCount)
        XCTAssertEqual(set.sha256, set.manifest?.jsonlSHA256)
        XCTAssertEqual(set.manifest?.schema, "edat-phone-items-manifest/v1")
        for item in set.items { XCTAssertNotNil(item.todayComponents, item.id) }
    }

    // MARK: result

    func testResultHasSpecKeys() throws {
        let result = PhoneResult(
            id: "ph-seq-0012", trial: 2, mode: "messages", device: "iPhone Air 12 GB",
            runtime: PhoneRuntime(mlxSwiftLM: "3.32.3", mlxSwift: "0.32.3", toolFormat: "gemma4",
                                  recovery: "disabled", schemaValidation: "record_only"),
            weightsSHA256: nil, rawText: "line one\nline two",
            parsedCalls: [PhoneParsedCall(name: "update_event", argumentsRaw: "{}", arguments: .object([:]))],
            finishReason: "stop", promptTokens: 1834, completionTokens: 41, ttftS: nil, totalS: 1.5,
            localPass: true, modelID: "gemma-wisp")
        let line = try result.jsonlLine()
        XCTAssertFalse(line.contains(0x0A))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: line) as? [String: Any])
        let expected: Set<String> = [
            "schema", "id", "trial", "mode", "device", "runtime", "weights_sha256", "raw_text", "reasoning_text",
            "parsed_calls", "rejected_calls", "finish_reason", "prompt_tokens", "completion_tokens", "ttft_s",
            "total_s", "local_pass", "local_labels", "model_id", "status", "error"]
        XCTAssertEqual(Set(obj.keys), expected)
        XCTAssertEqual(obj["schema"] as? String, "edat-phone-result/v1")
        XCTAssertEqual(obj["status"] as? String, "ok")
        XCTAssertEqual(Set((try XCTUnwrap(obj["runtime"] as? [String: Any])).keys),
                       ["mlx_swift_lm", "mlx_swift", "tool_format", "recovery", "schema_validation"])
        let first = try XCTUnwrap((obj["parsed_calls"] as? [[String: Any]])?.first)
        XCTAssertEqual(Set(first.keys), ["name", "arguments_raw", "arguments"])
        XCTAssertEqual(try JSONDecoder().decode(PhoneResult.self, from: line), result)
    }
}
