﻿using System;
using System.Collections.Generic;

namespace upload_function.DB.Flat {
    public partial class FromxmlBackup {
        public string CallNumber { get; set; }
        public string Commodity { get; set; }
        public string CommodityType { get; set; }
        public string Type { get; set; }
        public string ShortDescription { get; set; }
        public string Description { get; set; }
        public string ShowDatePosted { get; set; }
        public string ClosingDate { get; set; }
        public string SiteMeeting { get; set; }
        public string ShowBuyerNameList { get; set; }
        public string BuyerPhoneShow { get; set; }
        public string BuyerEmailShow { get; set; }
        public string Division { get; set; }
        public string BuyerLocationShow { get; set; }
        public string Urls { get; set; }
        public string Parsedtext { get; set; }
        public DateTime Lastupdated { get; set; }
        public string Uuid { get; set; }
    }
}