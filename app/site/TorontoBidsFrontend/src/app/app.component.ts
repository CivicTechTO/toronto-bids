import { Component } from '@angular/core';
import { NgbDateStruct,NgbDate, NgbCalendar, NgbDateParserFormatter, NgbDatepickerModule, NgbDropdown, NgbModule, NgbDropdownModule } from '@ng-bootstrap/ng-bootstrap';
import { FormsModule } from '@angular/forms';
import { JsonPipe } from '@angular/common';
import { Commodity, CommodityType, SearchQuery } from './models/models';
import { MatNativeDateModule } from '@angular/material/core';
import { MatDatepicker } from '@angular/material/datepicker';
import { MatLegacyCard as MatCard } from '@angular/material/legacy-card';
import { Observable } from 'rxjs';
@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.less'],
})
/*
  TODO
  - Render all the search pieces
  - Observable for
    - Division
    - Type
    - Commodity
    - Commodity Type
    - Buyer

  - Create service

  -




*/

export class AppComponent {
  title = 'TorontoBidsFrontend';
  divisions = ['Division A', 'Division B', 'Division C'];
  commodities = [{value:CommodityType.Any,display:'Any'},{value:CommodityType.ConstructionServices,display:'Construction Services'}, {value:CommodityType.GoodsAndServices,display:'Goods and Services'}, {value:CommodityType.ProfessionalServices,display:'Professional Services'}];
  types = ['Type A', 'Type B', 'Type C'];
  buyer = '';

  searchQuery : SearchQuery;
  startDate?: NgbDateStruct;
  endDate?: NgbDateStruct;

	hoveredDate: NgbDate | null = null;

	postingStartDate: NgbDate | null;
	postingEndDate: NgbDate | null;
  closingStartDate : NgbDate | null;
  closingEndDate: NgbDate | null;
  commodityType: CommodityType;

	constructor(private calendar: NgbCalendar, public formatter: NgbDateParserFormatter) {
		this.postingStartDate = null;
		this.postingEndDate = calendar.getToday();
    this.closingStartDate = null;
    this.closingEndDate = null;
    this.commodityType = CommodityType.Any;
    this.searchQuery = {
      postingStartDate : null,
      postingEndDate : null,
      closingStartDate : null,
      closingEndDate : null,
      buyer : '',
      commodityType : CommodityType.Any,
      commodity:Commodity.Any,
      type:'',
      division:''
    }
	}

	onDateSelection(date: NgbDate) {
		if (!this.postingStartDate && !this.postingEndDate) {
			this.postingStartDate = date;
		} else if (this.postingStartDate && !this.postingEndDate && date && date.after(this.postingStartDate)) {
			this.postingEndDate = date;
		} else {
			this.postingEndDate = null;
			this.postingStartDate = date;
		}
	}

	isHovered(date: NgbDate) {
		return (
			this.postingStartDate && !this.postingEndDate && this.hoveredDate && date.after(this.postingStartDate) && date.before(this.hoveredDate)
		);
	}

	isInside(date: NgbDate) {
		return this.postingEndDate && date.after(this.postingStartDate) && date.before(this.postingEndDate);
	}

	isRange(date: NgbDate) {
		return (
			date.equals(this.postingStartDate) ||
			(this.postingEndDate && date.equals(this.postingEndDate)) ||
			this.isInside(date) ||
			this.isHovered(date)
		);
	}

	validateInput(currentValue: NgbDate | null, input: string): NgbDate | null {
		const parsed = this.formatter.parse(input);
		return parsed && this.calendar.isValid(NgbDate.from(parsed)) ? NgbDate.from(parsed) : currentValue;
	}
}
