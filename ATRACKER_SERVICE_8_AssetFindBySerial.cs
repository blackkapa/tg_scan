// Сервис A-Tracker ID8 (asset_find_by_serial): поиск активов по серийному номеру.
// GET /Api/Service?id=<asset_find_by_serial_service_id>&SerialNo=<serial>
//
// Возвращает список строк itamPortfolio с ключевыми полями для веб-подтверждения заявки:
// - ID
// - sFullName
// - sSerialNo
// - sInventNumber
// - lUserId.sFullName (владелец)
// - lt_lLocationId.sFullName
// - lt_lCategoryId.sFullName
//
// Важно: после создания сервиса в A-Tracker пропишите его ID в config.ini:
// [atracker]
// asset_find_by_serial_service_id = 8

try
{
    object serialObj = null;
    if (args.ContainsKey("SerialNo")) serialObj = args["SerialNo"];
    else if (args.ContainsKey("serialNo")) serialObj = args["serialNo"];
    else if (args.ContainsKey("sSerialNo")) serialObj = args["sSerialNo"];

    var serial = serialObj == null ? "" : serialObj.ToString().Trim();
    if (string.IsNullOrWhiteSpace(serial))
    {
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        returnCode = "Error";
        message = "Не передан параметр SerialNo";
    }
    else
    {
        var fields = new System.Collections.Generic.List<string>()
        {
            "ID",
            "sFullName",
            "sSerialNo",
            "sInventNumber",
            "lUserId",
            "lUserId.sFullName",
            "lt_lLocationId",
            "lt_lLocationId.sFullName",
            "lt_lCategoryId",
            "lt_lCategoryId.sFullName"
        };

        // Нормализованный поиск СТРОГО по серийному номеру:
        // используем только поле sSerialNo (без fallback на другие поля).
        var assets = new itamDataObject(
            "itamPortfolio",
            fields: fields,
            where:
                "REPLACE(REPLACE(REPLACE(REPLACE(LOWER(ISNULL([sSerialNo], '')), ' ', ''), '-', ''), '/', ''), '_', '') = " +
                "REPLACE(REPLACE(REPLACE(REPLACE(LOWER(@P1), ' ', ''), '-', ''), '/', ''), '_', '')",
            parameters: new System.Collections.Generic.Dictionary<string, object>()
            {
                { "@P1", serial }
            }
        );

        var rows = assets.Rows ?? new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        data = rows;
        returnCode = "Success";
        message = "";
    }
}
catch (System.Exception ex)
{
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    returnCode = "Error";
    message = ex.ToString();
}
