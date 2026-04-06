// Сервис A-Tracker (GET): GetAssetAddRequestState
// Назначение: отдать состояние заявки для веба перед закрытием.
// Вход query:
// - RequestId (int, обяз.)
//
// Выход:
// data[0] = { ID, sReqNumber, seStatus, lRequesterId, lResolvedUserId?, lChosenPortfolioId? }

try
{
    int ToInt(object o)
    {
        if (o == null) return 0;
        int x;
        return int.TryParse(o.ToString(), out x) ? x : 0;
    }

    object requestIdObj = null;
    if (args != null)
    {
        if (args.ContainsKey("RequestId")) requestIdObj = args["RequestId"];
        else if (args.ContainsKey("requestId")) requestIdObj = args["requestId"];
    }
    int requestId = ToInt(requestIdObj);
    if (requestId <= 0)
    {
        returnCode = "Error";
        message = "Не передан RequestId.";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        var fields = new System.Collections.Generic.List<string>
        {
            "ID",
            "sReqNumber",
            "seStatus",
            "lRequesterId",
            // Опциональные поля ниже — оставляем, если они есть в вашей модели:
            // "lResolvedUserId",
            // "lChosenPortfolioId"
        };

        var req = new itamDataObject(
            "itamRequest",
            fields: fields,
            where: "[ID] = @P1",
            parameters: new System.Collections.Generic.Dictionary<string, object> { { "@P1", requestId } }
        );

        if (req.Rows == null || req.Rows.Count == 0)
        {
            returnCode = "Error";
            message = "Заявка не найдена.";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }
        else
        {
            var row = req.Rows[0];
            var outRow = new System.Collections.Generic.Dictionary<string, object>();
            foreach (var kv in row) outRow[kv.Key] = kv.Value;

            returnCode = "Success";
            message = "";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>> { outRow };
        }
    }
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
