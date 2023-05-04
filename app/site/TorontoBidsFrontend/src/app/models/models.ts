export class SearchQuery{
  postingStartDate:string|null = null;
  postingEndDate?:string|null = null;
  closingStartDate?:string|null = null;
  closingEndDate?:string|null = null;
  buyer:string|null= null;
  commodityType:CommodityType=CommodityType.Any;
  commodity:Commodity = Commodity.Any;
  type:string|null = null;
  division:string|null = null;
}

export enum Commodity {
  Any = 0,
  ConstructionServices = 1,
  GoodsAndServices = 2,
  ProfessionalServices = 3,
  Unknown = 4
}

export enum CommodityType {
  Any = 0,
  ConstructionServices = 1,
  GoodsAndServices = 2,
  ProfessionalServices = 3,
  Unknown = 4
}
