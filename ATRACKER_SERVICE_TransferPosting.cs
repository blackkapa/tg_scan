// Сервис A-Tracker: утверждение перемещения активов (POST) — логика как в мастере OneLineTransit2 (transitionScript).
// Веб: POST /Api/Service?id=<transfer_posting_service_id>, JSON: lUserIdFrom, lUserIdTo, portfolioIds[], seOrganization (строка «ООО …»), lReceiverLocationId.

try
{
    long ToLong(object o)
    {
        if (o == null) return 0;
        if (o is long l) return l;
        if (o is int i) return i;
        if (long.TryParse(o.ToString(), out var x)) return x;
        return 0;
    }

    long ToLongElem(System.Text.Json.JsonElement el)
    {
        if (el.ValueKind == System.Text.Json.JsonValueKind.Number && el.TryGetInt64(out var n)) return n;
        if (el.ValueKind == System.Text.Json.JsonValueKind.String && long.TryParse(el.GetString(), out var m)) return m;
        return 0;
    }

    System.Collections.Generic.List<long> ParseIdList(object rawList)
    {
        var list = new System.Collections.Generic.List<long>();
        if (rawList == null) return list;
        var arr = rawList as System.Collections.IEnumerable;
        if (arr == null) return list;
        foreach (var item in arr)
        {
            var v = ToLong(item);
            if (v > 0) list.Add(v);
        }
        return list;
    }

    string StrRow(System.Collections.Generic.Dictionary<string, object> row, params string[] keys)
    {
        foreach (var k in keys)
        {
            if (row != null && row.ContainsKey(k) && row[k] != null)
                return row[k].ToString();
        }
        return "";
    }

    int IntRow(System.Collections.Generic.Dictionary<string, object> row, string key, int def = 1)
    {
        if (row == null || !row.ContainsKey(key) || row[key] == null) return def;
        try { return System.Convert.ToInt32(row[key]); } catch { return def; }
    }

    long CategoryIdFromRow(System.Collections.Generic.Dictionary<string, object> row)
    {
        if (row == null) return 0;
        if (row.ContainsKey("lt_lCategoryId") && row["lt_lCategoryId"] != null)
        {
            var o = row["lt_lCategoryId"];
            if (o is System.Collections.IDictionary od)
            {
                if (od.Contains("ID")) return ToLong(od["ID"]);
                if (od.Contains("Id")) return ToLong(od["Id"]);
            }
            return ToLong(o);
        }
        foreach (var k in new[] { "lCategoryId", "LCategoryId" })
            if (row.ContainsKey(k) && row[k] != null) return ToLong(row[k]);
        return 0;
    }

    string CategoryNameFromRow(System.Collections.Generic.Dictionary<string, object> row)
    {
        long cid = CategoryIdFromRow(row);
        if (cid > 0)
        {
            var cat = new itamDataObject("itamCategory", "[ID] = " + cid);
            if (cat.Rows != null && cat.Rows.Count > 0)
            {
                var nm = StrRow(cat.Rows[0], "sFullName", "Name", "SFullName");
                if (!string.IsNullOrWhiteSpace(nm)) return nm;
            }
        }
        return StrRow(row, "lt_lCategoryId", "Lt_lCategoryId");
    }

    void SetPortfolioLocation(System.Collections.Generic.Dictionary<string, object> x, long locId)
    {
        int lid = (int)locId;
        if (x == null) return;
        // Выставляем все встречающиеся в строке ключи FK локации (пакетный Update часто трогает не те поля).
        var keys = new[] { "lt_lLocationId", "Lt_lLocationId", "lLocationId", "L_LocationId", "L_LocationID" };
        var any = false;
        foreach (var k in keys)
        {
            if (x.ContainsKey(k))
            {
                x[k] = lid;
                any = true;
            }
        }
        if (!any)
            x["lt_lLocationId"] = lid;
    }

    long lUserIdFrom = 0;
    long lUserIdTo = 0;
    string seOrganization = "";
    long lReceiverLocationId = 0;
    var portfolioIds = new System.Collections.Generic.List<long>();

    if (args != null && args.ContainsKey("__json") && args["__json"] != null)
    {
        object j = args["__json"];
        var dict = j as System.Collections.IDictionary;
        if (dict != null)
        {
            lUserIdFrom = ToLong(dict["lUserIdFrom"] ?? dict["LUserIdFrom"]);
            lUserIdTo = ToLong(dict["lUserIdTo"] ?? dict["LUserIdTo"]);
            seOrganization = (dict["seOrganization"] ?? dict["SeOrganization"] ?? "").ToString() ?? "";
            lReceiverLocationId = ToLong(dict["lReceiverLocationId"] ?? dict["LReceiverLocationId"]);
            portfolioIds = ParseIdList(dict["portfolioIds"] ?? dict["PortfolioIds"]);
        }
        else
        {
            string body = j != null ? j.ToString() : null;
            if (!string.IsNullOrWhiteSpace(body) && body.TrimStart().StartsWith("{"))
            {
                var doc = System.Text.Json.JsonDocument.Parse(body);
                var root = doc.RootElement;
                System.Text.Json.JsonElement p;
                if (root.TryGetProperty("lUserIdFrom", out p)) lUserIdFrom = ToLongElem(p);
                else if (root.TryGetProperty("LUserIdFrom", out p)) lUserIdFrom = ToLongElem(p);
                if (root.TryGetProperty("lUserIdTo", out p)) lUserIdTo = ToLongElem(p);
                else if (root.TryGetProperty("LUserIdTo", out p)) lUserIdTo = ToLongElem(p);
                if (root.TryGetProperty("seOrganization", out p)) seOrganization = p.GetString() ?? "";
                else if (root.TryGetProperty("SeOrganization", out p)) seOrganization = p.GetString() ?? "";
                if (root.TryGetProperty("lReceiverLocationId", out p)) lReceiverLocationId = ToLongElem(p);
                else if (root.TryGetProperty("LReceiverLocationId", out p)) lReceiverLocationId = ToLongElem(p);
                if (root.TryGetProperty("portfolioIds", out p) && p.ValueKind == System.Text.Json.JsonValueKind.Array)
                {
                    foreach (var el in p.EnumerateArray())
                        portfolioIds.Add(ToLongElem(el));
                }
                else if (root.TryGetProperty("PortfolioIds", out p) && p.ValueKind == System.Text.Json.JsonValueKind.Array)
                {
                    foreach (var el in p.EnumerateArray())
                        portfolioIds.Add(ToLongElem(el));
                }
            }
        }
    }

    if (lUserIdFrom <= 0 || lUserIdTo <= 0)
    {
        returnCode = "Error";
        message = "Укажите lUserIdFrom и lUserIdTo (положительные ID из itamEmplDept).";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else if (portfolioIds == null || portfolioIds.Count == 0)
    {
        returnCode = "Error";
        message = "Укажите непустой массив portfolioIds (ID записей itamPortfolio).";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        string fail = null;

        var giver = new itamDataObject("itamEmplDept", "[ID] = " + lUserIdFrom);
        if (giver.Rows == null || giver.Rows.Count == 0)
            fail = "Не найден отправитель itamEmplDept ID=" + lUserIdFrom;

        var receiverUser = new itamDataObject("itamEmplDept", "[ID] = " + lUserIdTo);
        if (fail == null && (receiverUser.Rows == null || receiverUser.Rows.Count == 0))
            fail = "Не найден получатель itamEmplDept ID=" + lUserIdTo;

        string sGiverName = "";
        string sReceiverName = "";
        if (fail == null)
        {
            sGiverName = StrRow(giver.Rows[0], "sFullName", "SFullName");
            sReceiverName = StrRow(receiverUser.Rows[0], "sFullName", "SFullName");
        }

        string sReceiverCity = "";
        if (fail == null && lReceiverLocationId > 0)
        {
            var loc = new itamDataObject("itamLocation", "[ID] = " + lReceiverLocationId);
            if (loc.Rows != null && loc.Rows.Count > 0)
                sReceiverCity = StrRow(loc.Rows[0], "sFullName", "SFullName");
        }

        var assets = new itamDataObject("itamPortfolio");
        assets.Rows = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();

        if (fail == null)
        {
            foreach (var pid in portfolioIds)
            {
                var one = new itamDataObject("itamPortfolio", "[ID] = " + pid);
                if (one.Rows == null || one.Rows.Count < 1)
                {
                    fail = "Не найден актив itamPortfolio ID=" + pid;
                    break;
                }
                assets.Rows.Add(one.Rows[0]);
            }
        }

        if (fail == null && (assets.Rows == null || assets.Rows.Count != portfolioIds.Count))
            fail = "Не удалось загрузить все активы по portfolioIds.";

        string fromLocationStrForWord = "";
        if (fail == null)
        {
            // «Откуда» для Word — до смены локации в БД (исходные строки в памяти).
            var fromLocSetBefore = new System.Collections.Generic.List<string>();
            foreach (var row in assets.Rows)
            {
                var locStr = StrRow(row, "lt_lLocationId");
                if (!string.IsNullOrEmpty(locStr) && !fromLocSetBefore.Contains(locStr))
                    fromLocSetBefore.Add(locStr);
            }
            fromLocationStrForWord = string.Join(", ", fromLocSetBefore);

            int uidTo = (int)lUserIdTo;
            // По одному активу: одиночный Update() надёжнее сохраняет lUserId и lt_lLocationId.
            foreach (var pid in portfolioIds)
            {
                var one = new itamDataObject("itamPortfolio", "[ID] = " + pid);
                if (one.Rows == null || one.Rows.Count < 1)
                {
                    fail = "Не найден актив itamPortfolio ID=" + pid;
                    break;
                }
                var x = one.Rows[0];
                x["lUserId"] = uidTo;
                if (lReceiverLocationId > 0)
                    SetPortfolioLocation(x, lReceiverLocationId);
                one.Update();
            }
        }

        if (fail == null)
        {
            var operation = new itamDataObject("itamOperation", 1);
            operation.Rows[0]["seOperationType"] = 1;
            operation.Rows[0]["seMovementType"] = 0;
            operation.Rows[0]["sFullName"] = "Перемещение ИТ-активов между пользователями";
            operation.Insert();

            int opId = (int)operation.Rows[0]["ID"];

            var relOperationAssets = new itamDataObject("relOperationAssets", assets.Rows.Count);
            for (int i = 0; i < assets.Rows.Count; i++)
            {
                relOperationAssets.Rows[i]["lPortfolioId"] = (int)assets.Rows[i]["ID"];
                relOperationAssets.Rows[i]["lOperationId"] = opId;
            }
            relOperationAssets.Insert();

            TableContent tableContent = new TableContent("Таблица активов");
            var idx = 1;
            foreach (var row in assets.Rows)
            {
                tableContent.AddRow(
                    new FieldContent("Порядковый номер", idx.ToString()),
                    new FieldContent("Полное имя актива", StrRow(row, "sFullName", "Name")),
                    new FieldContent("Категория актива", CategoryNameFromRow(row)),
                    new FieldContent("Тип", CategoryNameFromRow(row)),
                    new FieldContent("Инвентарный номер", StrRow(row, "sInventNumber", "sInventoryNo", "sInventNo")),
                    new FieldContent("Серийный номер", StrRow(row, "sSerialNo")),
                    new FieldContent("Количество", IntRow(row, "iQty", 1).ToString())
                );
                idx++;
            }

            var sOrg = string.IsNullOrWhiteSpace(seOrganization) ? "" : seOrganization.Trim();
            var valuesToFill = new Content(
                tableContent,
                new FieldContent("Организация", sOrg),
                new FieldContent("Откуда", fromLocationStrForWord ?? ""),
                new FieldContent("Куда", sReceiverCity ?? ""),
                new FieldContent("Дата", System.DateTime.Now.ToString("dd.MM.yyyy")),
                new FieldContent("Полное имя сотрудника", sReceiverName ?? ""),
                new FieldContent("Полное имя представителя", sGiverName ?? ""),
                new FieldContent("Номер операции", opId.ToString("D8"))
            );

            var word = new itamWordTemplate("Акт передачи оборудования");
            var fileBytes = word.Generate(valuesToFill);

            string safeGiver = string.IsNullOrEmpty(sGiverName) ? "отправитель" : sGiverName;
            string safeRecv = string.IsNullOrEmpty(sReceiverName) ? "получатель" : sReceiverName;
            var fileName = "Акт передачи оборудования от " + safeGiver + " к " + safeRecv + ".docx";

            new HelperMethods().SaveFile(fileName, fileBytes, "itamOperation", opId);

            returnCode = "Success";
            message = "Перемещение проведено";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
            {
                new System.Collections.Generic.Dictionary<string, object> { ["operationId"] = opId }
            };
        }
        else
        {
            returnCode = "Error";
            message = fail;
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }
    }
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
