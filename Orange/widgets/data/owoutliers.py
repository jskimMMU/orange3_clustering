import numpy as np
from AnyQt.QtWidgets import QLayout

from Orange.base import SklLearner
from Orange.classification import OneClassSVMLearner, EllipticEnvelopeLearner
from Orange.data import Table, Domain, ContinuousVariable
from Orange.widgets import gui
from Orange.widgets.settings import Setting
from Orange.widgets.utils.sql import check_sql_input
from Orange.widgets.utils.widgetpreview import WidgetPreview
from Orange.widgets.widget import Msg, Input, Output, OWWidget


class OWOutliers(OWWidget):
    name = "Outliers"
    description = "Detect outliers."
    icon = "icons/Outliers.svg"
    priority = 3000
    category = "Data"
    keywords = ["inlier"]

    class Inputs:
        data = Input("Data", Table)

    class Outputs:
        inliers = Output("Inliers", Table)
        outliers = Output("Outliers", Table)

    want_main_area = False

    OneClassSVM, Covariance = range(2)

    outlier_method = Setting(OneClassSVM)
    nu = Setting(50)
    gamma = Setting(0.01)
    cont = Setting(10)
    empirical_covariance = Setting(False)
    support_fraction = Setting(1)

    MAX_FEATURES = 1500

    class Warning(OWWidget.Warning):
        disabled_cov = Msg("Too many features for covariance estimation.")

    class Error(OWWidget.Error):
        singular_cov = Msg("Singular covariance matrix.")
        memory_error = Msg("Not enough memory")

    def __init__(self):
        super().__init__()
        self.data = None
        self.n_inliers = self.n_outliers = None

        box = gui.vBox(self.controlArea, "Outlier Detection Method")
        detection = gui.radioButtons(box, self, "outlier_method")

        gui.appendRadioButton(detection,
                              "One class SVM with non-linear kernel (RBF)")
        ibox = gui.indentedBox(detection)
        tooltip = "An upper bound on the fraction of training errors and a " \
                  "lower bound of the fraction of support vectors"
        gui.widgetLabel(ibox, 'Nu:', tooltip=tooltip)
        self.nu_slider = gui.hSlider(
            ibox, self, "nu", minValue=1, maxValue=100, ticks=10,
            labelFormat="%d %%", callback=self.nu_changed, tooltip=tooltip)
        self.gamma_spin = gui.spin(
            ibox, self, "gamma", label="Kernel coefficient:", step=1e-2,
            spinType=float, minv=0.01, maxv=10, callback=self.gamma_changed)
        gui.separator(detection, 12)

        self.rb_cov = gui.appendRadioButton(detection, "Covariance estimator")
        ibox = gui.indentedBox(detection)
        self.l_cov = gui.widgetLabel(ibox, 'Contamination:')
        self.cont_slider = gui.hSlider(
            ibox, self, "cont", minValue=0, maxValue=100, ticks=10,
            labelFormat="%d %%", callback=self.cont_changed)

        ebox = gui.hBox(ibox)
        self.cb_emp_cov = gui.checkBox(
            ebox, self, "empirical_covariance",
            "Support fraction:", callback=self.empirical_changed)
        self.support_fraction_spin = gui.spin(
            ebox, self, "support_fraction", step=1e-1, spinType=float,
            minv=0.1, maxv=10, callback=self.support_fraction_changed)

        gui.separator(detection, 12)

        gui.button(self.buttonsArea, self, "Detect Outliers",
                   callback=self.commit)
        self.layout().setSizeConstraint(QLayout.SetFixedSize)

        self.info.set_input_summary(self.info.NoInput)
        self.info.set_output_summary(self.info.NoOutput)

    def nu_changed(self):
        self.outlier_method = self.OneClassSVM

    def gamma_changed(self):
        self.outlier_method = self.OneClassSVM

    def cont_changed(self):
        self.outlier_method = self.Covariance

    def support_fraction_changed(self):
        self.outlier_method = self.Covariance

    def empirical_changed(self):
        self.outlier_method = self.Covariance

    def enable_covariance(self, enable=True):
        self.rb_cov.setEnabled(enable)
        self.l_cov.setEnabled(enable)
        self.cont_slider.setEnabled(enable)
        self.cb_emp_cov.setEnabled(enable)
        self.support_fraction_spin.setEnabled(enable)

    @Inputs.data
    @check_sql_input
    def set_data(self, data):
        self.clear_messages()
        self.data = data
        self.info.set_input_summary(len(data) if data else self.info.NoOutput)
        self.enable_controls()
        self.commit()

    def enable_controls(self):
        self.enable_covariance()
        if self.data and len(self.data.domain.attributes) > self.MAX_FEATURES:
            self.outlier_method = self.OneClassSVM
            self.enable_covariance(False)
            self.Warning.disabled_cov()

    def _get_outliers(self):
        self.Error.singular_cov.clear()
        self.Error.memory_error.clear()
        try:
            y_pred, amended_data = self.detect_outliers()
        except ValueError:
            self.Error.singular_cov()
            return None, None
        except MemoryError:
            self.Error.memory_error()
            return None, None
        else:
            inliers_ind = np.where(y_pred == 1)[0]
            outliers_ind = np.where(y_pred == -1)[0]
            inliers = amended_data[inliers_ind]
            outliers = amended_data[outliers_ind]
            self.n_inliers = len(inliers)
            self.n_outliers = len(outliers)

            return inliers, outliers

    def commit(self):
        inliers = outliers = None
        self.n_inliers = self.n_outliers = None
        if self.data:
            inliers, outliers = self._get_outliers()

        summary = len(inliers) if inliers else self.info.NoOutput
        self.info.set_output_summary(summary)
        self.Outputs.inliers.send(inliers)
        self.Outputs.outliers.send(outliers)

    def detect_outliers(self):
        if self.outlier_method == self.OneClassSVM:
            learner = OneClassSVMLearner(
                gamma=self.gamma, nu=self.nu / 100,
                preprocessors=SklLearner.preprocessors)
        else:
            learner = EllipticEnvelopeLearner(
                support_fraction=self.support_fraction
                if self.empirical_covariance else None,
                contamination=self.cont / 100.)
        model = learner(self.data)
        y_pred = model(self.data)
        amended_data = self.amended_data(model)
        return np.array(y_pred), amended_data

    def amended_data(self, model):
        if self.outlier_method != self.Covariance:
            return self.data
        mahal = model.mahalanobis(self.data.X)
        mahal = mahal.reshape(len(self.data), 1)
        attrs = self.data.domain.attributes
        classes = self.data.domain.class_vars
        new_metas = list(self.data.domain.metas) + \
                    [ContinuousVariable(name="Mahalanobis")]
        new_domain = Domain(attrs, classes, new_metas)
        amended_data = self.data.transform(new_domain)
        amended_data.metas = np.hstack((self.data.metas, mahal))
        return amended_data

    def send_report(self):
        if self.n_outliers is None or self.n_inliers is None:
            return
        self.report_items("Data",
                          (("Input instances", len(self.data)),
                           ("Inliers", self.n_inliers),
                           ("Outliers", self.n_outliers)))
        if self.outlier_method == 0:
            self.report_items(
                "Detection",
                (("Detection method",
                  "One class SVM with non-linear kernel (RBF)"),
                 ("Regularization (nu)", self.nu),
                 ("Kernel coefficient", self.gamma)))
        else:
            self.report_items(
                "Detection",
                (("Detection method", "Covariance estimator"),
                 ("Contamination", self.cont),
                 ("Support fraction", self.support_fraction)))


if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWOutliers).run(Table("iris"))
