using System;
using System.IO;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Extensions.Http;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Newtonsoft.Json;
using System.Linq;
using upload_function.DB.Flat;
using System.Collections.Generic;

namespace upload_function
{
    public class UploadedEntry {
        public string Commodity { get; set; }
        public string CommodityType { get; set; }
        public string CallNumber { get; set; }
        public string Type { get; set; }

        public string ShortDescription { get; set; }
        public string Description { get; set; }
        public string ShowDatePosted { get; set; }
        public string ShowDateClosing { get; set; }
        public string SiteMeeting { get; set; }
        public string ShowBuyerNameList { get; set; }
        public string BuyerPhoneShow { get; set; }
        public string BuyerEmailShow { get; set; }
        public string Division { get; set; }
        public string PickFee { get; set; }
        public string SecurityDeposit { get; set; }
        public string BuyerLocationShow { get; set; }

        public string AllAttachments { get; set; }

        public List<string> FileName { get; set; }
        public List<string> Location { get; set; }
        public List<string> DownloadLink { get; set; }
    }

    public class UploadedEntryParent {
        public List<UploadedEntry> list { get; set; }
    }
    public static class FlatUpload
    {
        [FunctionName("FlatUpload")]
        public static async Task<IActionResult> Run(
            [HttpTrigger(AuthorizationLevel.Anonymous, "get", "post", Route = null)] HttpRequest req,
            ILogger log)
        {
            log.LogInformation("C# HTTP trigger function processed a request.");
            try
            {
               
                string requestBody = await new StreamReader(req.Body).ReadToEndAsync();

                var dataList = JsonConvert.DeserializeObject<UploadedEntryParent>(requestBody);
                using var context = new FlatDBContext();

                foreach (var data in dataList.list)
                {
                    string callNumber = data.CallNumber;
                    log.LogInformation($"Processing call with call number : {callNumber}");
                    var dbDoc = context.Calls.FirstOrDefault(x => x.CallNumber == callNumber);
                    if (dbDoc == null)
                    {
                        dbDoc = new Call
                        {
                            CallNumber = (data?.CallNumber == null ? "" : data?.CallNumber),
                            Commodity = (data?.Commodity == null ? "" : data?.Commodity),
                            CommodityType = (data?.CommodityType == null ? "" : data?.CommodityType),
                            Division = (data?.Division == null ? "" : data?.Division),
                            Type = (data?.Type == null ? "" : data?.Type),
                            ShortDescription = (data?.ShortDescription == null ? "" : data?.ShortDescription),
                            Description = (data?.Description == null ? "" : data?.Description),
                            ShowDatePosted = DateOnly.MinValue,
                            ClosingDate = DateOnly.MinValue,
                            SiteMeeting = (data?.SiteMeeting == null ? "" : data?.SiteMeeting),
                            ShowBuyerNameList = (data?.ShowBuyerNameList == null ? "" : data?.ShowBuyerNameList),
                            BuyerPhoneShow = (data?.BuyerPhoneShow == null ? "" : data?.BuyerPhoneShow),
                            BuyerEmailShow = (data?.BuyerEmailShow == null ? "" : data?.BuyerEmailShow),
                            BuyerLocationShow = (data?.BuyerLocationShow == null ? "" : data?.BuyerLocationShow),
                            Lastupdated = DateTime.Now,
                            Uuid = ""
                        };

                        List<string> fileNames = data.FileName;
                     
                        if (fileNames != null)
                        {
                            for (int i = 0; i < fileNames.Count; i++)
                            {
                                var attachment = new Attachment
                                {
                                    CallNumber = (data?.CallNumber == null ? "" : data?.CallNumber),
                                    Filename = fileNames[i],
                                    Parsedtext = "",
                                    Lastupdated = DateTime.Now,
                                    Uuid = ""
                                };

                                context.Attachments.Add(attachment);
                            }
                        }


                        context.Calls.Add(dbDoc);

                    }

                    context.SaveChanges();

                    log.LogInformation("Saved successfully");
                }
            }

            catch (Exception e)
            {
                var errString = $"There was an error {e.Message} - {e.StackTrace}";
                log.LogInformation(errString);
                return new StatusCodeResult(500);
            }

            return new StatusCodeResult(200);
        }
    }
}
