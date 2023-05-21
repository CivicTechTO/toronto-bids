using System;
using System.Collections.Generic;

namespace upload_function.DB.Normalized;

public partial class Attachment {
    public int Id { get; set; }

    public int DocumentId { get; set; }

    public string FileName { get; set; }

    public string DatastoreFileURL { get; set; }

    public string AttachmentPath { get; set; }


    public virtual Document Document { get; set; }
}
