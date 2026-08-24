import datetime
import os
import csv
import pandas as pd
from collections import OrderedDict
from datetime import datetime
import numpy as np
import pdfreport as pdf
import calibration
from pyphantom_config.config import JsonConfig

class ReportHandler():
	def __init__(self):
		self.active_report = BaseReport()
		self._track = None
		self._meas = None
		self.active_type = ''
	
	def update_active_report(self, type: str):
		self.active_type = type
		if type == 'track':
			if self._track is None:
				self._track = TrackReport()
			self._track.sync_properties(self.active_report)
			self.active_report = self._track

		elif type == 'meas':
			if self._meas is None:
				self._meas = MeasReport()
			self._meas.sync_properties(self.active_report)
			self.active_report = self._meas

		elif type == 'base':
			self.active_type = 'base'
			self.active_report = BaseReport()

		return self.active_report
			
class BaseReport():
	#region ACCESSORS
	@property
	def cine_path(self):
		return self._cine_path
	@cine_path.setter
	def cine_path(self, new_path):
		self._cine_path = new_path.replace('/','\\')
		# reports are saved next to active cine with childname_datetimestamp
		self.report_path = os.path.join(os.path.dirname(self.cine_path), f'{type(self).__name__}_{datetime.now().strftime("%H%M%S_%m%d%Y")}.csv')

	#endregion

	def __init__(self):
		"""
		Initialize a new instance of the BaseReport class.

		Returns:
			None
		"""
		self.cine_path = ''
		self.report_path = ''
		self.delimiter = ","

		cfg_path = os.path.join(os.path.dirname(__file__), 'config.json')
		self._config = JsonConfig(cfg_path)

		# Default Delimeter
		val = self._config.get("delimiter")
		if val is not None:
			self.delimiter = val

	def sync_properties(self, current):
		"""Synchronizes the base report properties to the current values"""
		self.cine_path = current.cine_path

	def export_report(self, data: OrderedDict, metadata: OrderedDict=None):
		"""
		Converts a dictionary of data into a csv report
		Args:
			data: dictionary

		Returns:
			report_path: string path of saved report

		Method will convert all key items into a 2D array and save to csv.
		Column headers will be equal to the key
		Top of file header table can be defined by a dictionary with key '_header_info'
		"""
		header_key = '_header_info'
		if header_key in data:
			header_table = self._generate_header_table(data[header_key])
			self._write_2D_array_to_file(self.report_path, header_table, newfile=True)
		for k, d in data.items():
			if k[0] != '_':
				subtable = self._generate_subtable(d, k)
				self._write_2D_array_to_file(self.report_path, subtable)
			elif '_pf_' in k:
				self._write_preformatted_string_to_file(self.report_path, d, k.split('_')[-1])
			
		# remove trailing whitespace row
		if os.path.exists(self.report_path):
			f = open(self.report_path, "r+", encoding='utf-8')
			lines = f.readlines()
			lines.pop()
			f.close()
			f = open(self.report_path, "w+", encoding='utf-8')
			f.writelines(lines)
			f.close()

		return self.report_path

	def _generate_header_table(self, header: OrderedDict):
		header_table = list()
		for i, h in enumerate(header.keys()):
			col_data = [h] + [header[h]]
			header_table.append(col_data)
		return header_table

	def _generate_subtable(self, data: OrderedDict, key: str):
		# get subtable into 2D string array
		subtable = list()
		transpose = True
		if '_transpose' in data:
			transpose = bool(data['_transpose'])
			data.pop('_transpose')
		for k, v in data.items():
			col_data = [k] + (v if isinstance(v, list) else [v])
			subtable.append(col_data)
		subtable = np.array(subtable, dtype='U32')
		if transpose:
			subtable = np.transpose(subtable)
		subtable_header = np.empty_like(subtable[0])
		subtable_header[0] = key
		subtable = np.vstack((subtable_header, subtable))
		return subtable

	def _write_2D_array_to_file(self, path, array, newfile=False):
		try: 
			if newfile: os.remove(path)
		except: pass
		with open(path, 'a+', newline='', encoding='utf-8') as f:
			wr = csv.writer(f, delimiter=self.delimiter)
			wr.writerows(array)
			wr.writerow([])

	def _write_preformatted_string_to_file(self, path, string, key, newfile=False):
		try: 
			if newfile: os.remove(path)
		except: pass
		with open(path, 'a+', newline='', encoding='utf-8') as f:
			f.write(f"{key}\n")
			f.write(f"{string}\n\n")

class MeasReport(BaseReport):
	def export_report(self, data: OrderedDict, metadata: OrderedDict=None):
		data['_header_info'] = OrderedDict({'Date':datetime.now().strftime(r'%b-%d-%Y %H:%M:%S'), 'Cine File': self.cine_path})
		return super().export_report(data, metadata)

class TrackReport(BaseReport):
	def export_report(self, data: OrderedDict, metadata: OrderedDict=None):
		# to a formatted report dict that gets passed into export_report
		cal = calibration.Calibration()
		time_units = metadata['time_units']
		rep_data = OrderedDict()
		rep_data['_header_info'] = OrderedDict({'Date':datetime.now().strftime(r'%b-%d-%Y %H:%M:%S'), 'Cine File': self.cine_path,
												'Scale': metadata['scale'], 'Units': metadata['units']})
		rep_data['Cine MetaData'] = OrderedDict(metadata['other_md'])
		if len(data) > 0:
			rep_data['Objects'] = OrderedDict([(id, d['name']) for id, d in data.items()])
			rep_data['Objects']['_transpose'] = False

		first_fr = int(metadata['first_frame'])
		track_df = pd.DataFrame(columns=["Frame #", f"Time from Trigger ({time_units})", f"Time from Zero ({time_units})"])
		for id, d in data.items():
			cal_pts = cal.point_transform(d['points'])
			frames_cine = d['frames'] + first_fr
			columns = ["Frame #", f"Time from Trigger ({time_units})", f"Time from Zero ({time_units})", f"x{id}", f"y{id}", f"score_{id}"]
			object_df = pd.DataFrame(np.concatenate((frames_cine.reshape(-1,1), d['frame_ts_trig'].reshape(-1,1), 
											d['frame_ts'].reshape(-1,1), cal_pts, d['scores'].reshape(-1,1)), axis=1), columns=columns)
			track_df = track_df.merge(object_df, how='outer', on=["Frame #", f"Time from Trigger ({time_units})", f"Time from Zero ({time_units})"])
		track_df['Frame #'] = track_df['Frame #'].astype(int)
		track_df.sort_values('Frame #', inplace=True)
		rep_data['_pf_Data'] = track_df.to_csv(sep=self.delimiter, index=False)

		rp = super().export_report(rep_data)
		try:
			pdf_report_path = os.path.join(os.path.dirname(rp), f'{os.path.splitext(os.path.basename(rp))[0]}.pdf')
			pdf_rep = pdf.PDFReport(data, metadata, self.cine_path)
			pdf_rep.pdf.core_fonts_encoding = 'utf-8'
			pdf_rep.pdf.set_font('Times', '', 12)
			pdf_rep.generate_pdf(pdf_report_path)
		except Exception as e:
			raise TrackReportException(f'Error in generating PDF Track Report.\n{e}')

		return rp

class TrackReportException(Exception):
	def __init__(self, message):
		self.message = message
		super().__init__(self.message)