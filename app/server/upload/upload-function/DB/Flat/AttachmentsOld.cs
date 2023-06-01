using System;
using System.Collections.Generic;

namespace upload_function.DB.Flat {
    public partial class AttachmentsOld {
        public string CallNumber { get; set; }
        public string Filename { get; set; }
        public string Parsedtext { get; set; }
        public DateTime Lastupdated { get; set; }
        public string Uuid { get; set; }
    }
}
