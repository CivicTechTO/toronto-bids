using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Document {
    public int Id { get; set; }

    public int TypeId { get; set; }

    public string CallNumber { get; set; }

    public int CommodityTypeId { get; set; }

    public int DivisionId { get; set; }

    public string ShortDescription { get; set; }

    public string Description { get; set; }

    public string SearchText { get; set; }

    public DateOnly PostingDate { get; set; }

    public DateOnly ClosingDate { get; set; }

    public string SiteMeeting { get; set; }

    public DateTime LastUpdated { get; set; }

    public virtual ICollection<Attachment> Attachments { get; set; } = new List<Attachment>();

    public virtual CommodityType CommodityType { get; set; }

    public virtual Division Division { get; set; }

    public virtual OfferType OfferType { get; set; }
}
