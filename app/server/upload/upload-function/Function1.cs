using System;
using System.IO;
using System.Data.Common;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Extensions.Http;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Newtonsoft.Json;
using upload_function.DB;
using System.Collections.Generic;
using Type = upload_function.DB.OfferType;
using System.Linq;

namespace upload_function
{
    public static class UploadFunction
    {
        [FunctionName("upload-document")]
        public static async Task<IActionResult> Run(
            [HttpTrigger(AuthorizationLevel.Anonymous, "get", "post", Route = null)] HttpRequest req,
            ILogger log) {
            log.LogInformation("C# HTTP trigger function processed a request.");

            string name = req.Query["name"];

            try
            {
                using var streamReader = new StreamReader("C:\\code\\CivicTech\\toronto-bids\\app\\server\\upload\\upload-function\\test-sample.json");

                using var context = new BidsDBContext();
                string body = streamReader.ReadToEnd();
                dynamic data = JsonConvert.DeserializeObject(body);

                string callNumber = data.CallNumber;
                var dbDoc = context.Documents.FirstOrDefault(x => x.CallNumber == callNumber);
                if (dbDoc == null)
                {
                    var new_comm_type = new CommoditySubType
                    {
                        SubTypeName = data.CommodityType,
                        Commodity = new Commodity
                        {
                            CommodityName = data.Commodity,
                        }
                    };

                    var new_division = new Division
                    {
                        Division1 = data.Division
                    };

                    var new_location = new Location
                    {
                        Location1 = data.BuyerLocationShow
                    };

                    var new_buyer = new Buyer
                    {
                        Buyer1 = data.ShowBuyerNameList,
                        Phone = data.BuyerPhoneShow,
                        Email = data.BuyerEmailShow,
                        Location = new_location
                    };

                    var new_offer_type = new Type
                    {
                        Type1 = data.Type
                    };

                    var new_document = new Document
                    {
                        CallNumber = (data?.CallNumber == null ? "" : data?.CallNumber),
                        ShortDescription = (data?.ShortDescription == null ? "" : data?.ShortDescription),
                        Description = (data?.Description == null ? "" : data?.Description),
                        PostingDate = DateOnly.MinValue,
                        ClosingDate = DateOnly.MinValue,
                        SearchText = (data?.SearchText == null ? "" : data?.SearchText),
                        SiteMeeting = (data?.SiteMeeting == null ? "" : data?.SiteMeeting),
                        LastUpdated = DateTime.Now,
                        CommodityType = new_comm_type,
                        Division = new_division,
                        OfferType = new_offer_type,
                        Attachments = new List<Attachment>(),
                    };

                    string[] fileNames = data.FileName.ToObject<string[]>();
                    string[] fileURIs = data.DownloadLink.ToObject<string[]>();
                    string[] filePaths = data.Location.ToObject<string[]>();

                    var minlength = Math.Min(fileNames.Length, Math.Min(fileURIs.Length, filePaths.Length));

                    for (int i = 0; i < minlength; i++)
                    {
                        string uri = fileURIs[i];
                        uri = uri.Replace(@"\/", "\\");

                        string path = filePaths[i];
                        path = path.Replace(@"\/", "\\");

                        var attachment = new Attachment
                        {
                            FileName = fileNames[i],
                            DatastoreFileURL = uri,
                            AttachmentPath = path
                        };

                        new_document.Attachments.Add(attachment);
                    }

                    context.Documents.Add(dbDoc);

                }

                context.SaveChanges();

                log.LogInformation("Saved successfully");
               
            }

            catch(Exception e)
            {
                log.LogInformation($"There was an error {e.Message}");
            }

            string responseMessage = string.IsNullOrEmpty(name)
              ? "This HTTP triggered function executed successfully. Pass a name in the query string or in the request body for a personalized response."
              : $"Hello, {name}. This HTTP triggered function executed successfully.";

            return new OkObjectResult(responseMessage);
        }
    }
}
